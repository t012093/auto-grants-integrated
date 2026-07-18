# auto-grants-integrated 詳細仕様書

> **Version**: 2.0 (Integrated)  
> **更新日**: 2026-07-15  
> **ステータス**: Draft

---

## 1. データベース設計（Supabase PostgreSQL）

本プラットフォームは、助成金、予算フロー、自治体RAG、市民合意、ボランティア実行、およびクラウドファンディングのデータをSupabase PostgreSQL上に一元管理します。

### 1.1 コア認証・プロフィール（volunteer-connectより）

```sql
-- ユーザーロールの定義
CREATE TYPE public.app_user_role AS ENUM (
  'ADMIN',
  'EMPLOYEE',
  'CORPORATE',
  'NPO',
  'VOLUNTEER',
  'GOVERNMENT'
);

-- 組織タイプの定義
CREATE TYPE public.entity_kind AS ENUM (
  'COMPANY',
  'NPO'
);

-- トリガー用更新日時設定関数
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = TIMEZONE('utc', NOW());
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ユーザープロファイル
CREATE TABLE public.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  email TEXT UNIQUE,
  role public.app_user_role NOT NULL DEFAULT 'EMPLOYEE',
  display_name TEXT NOT NULL,
  department TEXT,
  avatar_url TEXT,
  bio TEXT,
  interest_areas TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  preferred_theme TEXT NOT NULL DEFAULT 'cosmic', -- 'cosmic' (Dark) or 'nordic' (Light)
  embedding vector(4096), -- ユーザープロファイル・スキルベクトル (Modal用)
  did TEXT UNIQUE, -- W3C標準の自己主権型分散ID (DID)
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);

CREATE TRIGGER set_profiles_updated_at
BEFORE UPDATE ON public.profiles
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- NPOプロファイル
CREATE TABLE public.npo_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id UUID NOT NULL UNIQUE REFERENCES public.profiles (id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  email TEXT,
  website TEXT,
  location TEXT,
  description TEXT NOT NULL DEFAULT '',
  needed_resources TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  activity_tags TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  target_audience TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);

CREATE TRIGGER set_npo_profiles_updated_at
BEFORE UPDATE ON public.npo_profiles
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- 企業プロファイル
CREATE TABLE public.company_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id UUID NOT NULL UNIQUE REFERENCES public.profiles (id) ON DELETE CASCADE,
  company_name TEXT NOT NULL,
  industry TEXT NOT NULL,
  employee_band TEXT,
  contact_email TEXT,
  mission TEXT,
  website TEXT,
  description TEXT NOT NULL DEFAULT '',
  resources TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  focus_areas TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  has_volunteer_leave BOOLEAN NOT NULL DEFAULT false,
  has_matching_gift BOOLEAN NOT NULL DEFAULT false,
  guidelines TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);

CREATE TRIGGER set_company_profiles_updated_at
BEFORE UPDATE ON public.company_profiles
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- 組織メンバー
CREATE TABLE public.members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_profile_id UUID REFERENCES public.company_profiles (id) ON DELETE CASCADE,
  npo_profile_id UUID REFERENCES public.npo_profiles (id) ON DELETE CASCADE,
  user_id UUID REFERENCES public.profiles (id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  role public.app_user_role NOT NULL,
  department TEXT,
  avatar_url TEXT,
  bio TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  -- 参照整合性を保証するため、いずれか片方の組織IDのみが非NULLであることを制約
  CONSTRAINT chk_members_org_exclusivity CHECK (
    (company_profile_id IS NOT NULL AND npo_profile_id IS NULL) OR
    (company_profile_id IS NULL AND npo_profile_id IS NOT NULL)
  )
);

CREATE TRIGGER set_members_updated_at
BEFORE UPDATE ON public.members
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- コア認証・プロフィール RLS (Row Level Security) ポリシー設定
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.npo_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.company_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.members ENABLE ROW LEVEL SECURITY;

-- 1. profiles: ユーザー自身は全権、他者は一部公開項目のみ参照可能
CREATE POLICY profile_self_policy ON public.profiles
  FOR ALL USING (id = auth.uid());
CREATE POLICY profile_public_read_policy ON public.profiles
  FOR SELECT USING (true);

-- 2. npo_profiles: 所有者のみ編集可能、他者は参照可能
CREATE POLICY npo_profile_owner_policy ON public.npo_profiles
  FOR ALL USING (owner_user_id = auth.uid());
CREATE POLICY npo_profile_public_read_policy ON public.npo_profiles
  FOR SELECT USING (true);

-- 3. company_profiles: 所有者のみ編集可能、他者は参照可能
CREATE POLICY company_profile_owner_policy ON public.company_profiles
  FOR ALL USING (owner_user_id = auth.uid());
CREATE POLICY company_profile_public_read_policy ON public.company_profiles
  FOR SELECT USING (true);

-- 4. members: 同一組織の所属メンバーのみが閲覧・編集可能
CREATE POLICY member_access_policy ON public.members
  FOR ALL USING (
    user_id = auth.uid() OR
    EXISTS (
      SELECT 1 FROM public.members m
      WHERE m.user_id = auth.uid() AND (
        (m.company_profile_id IS NOT NULL AND m.company_profile_id = public.members.company_profile_id) OR
        (m.npo_profile_id IS NOT NULL AND m.npo_profile_id = public.members.npo_profile_id)
      )
    )
  );
```

### 1.2 助成金・予算・自治体RAG（auto-grantsv2 & moneyflow-visualizer）

```sql
-- 助成金区分の定義 (公的・民間・クラファン等の分離識別用)
CREATE TYPE public.grant_category AS ENUM (
  'PUBLIC',         -- 公的助成金・補助金 (jGrants, 自治体公募)
  'PRIVATE',        -- 民間助成金 (民間財団、企業助成)
  'DONATION_CF'     -- クラウドファンディング等の寄付・資金調達
);

-- 助成金データ本体
CREATE TABLE public.grants (
  id SERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  provider TEXT NOT NULL,
  amount_max BIGINT,
  deadline DATE,
  details_url TEXT,
  category public.grant_category NOT NULL DEFAULT 'PUBLIC', -- 助成金区分
  source TEXT NOT NULL, -- データ取得元 (例: 'jgrants', 'toyama_pref', 'private_grant_portal_x')
  payload_json JSONB NOT NULL DEFAULT '{}'::JSONB,
  cascade_id INTEGER, -- 交付金カスケードIDへの参照 (Nullable)
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_grants_payload_data_hash ON public.grants ((payload_json->>'data_hash'));
CREATE INDEX idx_grants_cascade_id ON public.grants(cascade_id);
CREATE INDEX idx_grants_category ON public.grants(category);
CREATE INDEX idx_grants_source ON public.grants(source);

-- ベクトル知識チャンク (Modal GPU Embedding用)
CREATE TABLE public.knowledge_chunks (
  id SERIAL PRIMARY KEY,
  grant_id INTEGER REFERENCES public.grants(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  embedding vector(4096) NOT NULL, -- Modal 4096次元
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW インデックス（コサイン類似度用）
CREATE INDEX idx_knowledge_chunks_embedding_hnsw
ON public.knowledge_chunks USING hnsw (embedding vector_cosine_ops);

-- 予算フローノード (moneyflow-visualizer)
CREATE TABLE public.nodes (
  id TEXT PRIMARY KEY,                   -- 'JPN', 'MIN_CFA' 等の一意ID
  name TEXT NOT NULL,
  name_en TEXT,
  lat REAL,
  lng REAL,
  region TEXT,
  type TEXT NOT NULL,                    -- 'country', 'ministry', 'subsidy', 'recipient_org', 'vision', 'policy', 'project' 等
  dataset TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 予算フローエッジ
CREATE TABLE public.edges (
  id SERIAL PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES public.nodes(id),
  target_id TEXT NOT NULL REFERENCES public.nodes(id),
  category TEXT NOT NULL,                -- 'trade', 'subsidy_grant', 'subsidy_award' 等
  sub_category TEXT,
  value_usd REAL,                        -- 金額 (USD)
  value_jpy REAL,                        -- 金額 (JPY)
  -- ※正規化方針: 国内予算は JPY を正規値とし、USD は登録時の固定換算レート(例: 150 JPY/USD)で算出。
  --   国際データは USD を正規値とし、登録時の固定レートで JPY 換算値を算出。
  fiscal_year INTEGER,
  confidence REAL DEFAULT 1.0,
  source_dataset TEXT,
  grant_id INTEGER REFERENCES public.grants(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT uq_edges_src_tgt_fy UNIQUE (source_id, target_id, fiscal_year)
);

-- 予算データソース登録 (moneyflow-visualizer)
CREATE TABLE public.data_sources (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,             -- データソース名 (英語・一意制約)
  name_ja TEXT,                          -- 日本語表示名
  url TEXT,                              -- 情報元URL
  description TEXT,                      -- データソース概要
  fiscal_years TEXT,                     -- 対象年度範囲
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 助成金・予算・RAGデータ RLS (Row Level Security) ポリシー設定
ALTER TABLE public.grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.knowledge_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.data_sources ENABLE ROW LEVEL SECURITY;

-- 全ユーザーが SELECT (読み取り) 可能
CREATE POLICY grants_read_policy ON public.grants FOR SELECT USING (true);
CREATE POLICY knowledge_chunks_read_policy ON public.knowledge_chunks FOR SELECT USING (true);
CREATE POLICY nodes_read_policy ON public.nodes FOR SELECT USING (true);
CREATE POLICY edges_read_policy ON public.edges FOR SELECT USING (true);
CREATE POLICY data_sources_read_policy ON public.data_sources FOR SELECT USING (true);

-- ※ INSERT/UPDATE/DELETE に関するポリシーは定義しません。
--    これにより、Supabase 上の一般ユーザー (authenticated/anon) からの書き込みはすべてブロックされ、
--    RLSをバイパスする service_role キーを用いたバックエンドバッチ処理からのみ書き込み可能となります。
```

### 1.3 市民合意・投票（plurality-connect 新規実装分仕様）

```sql
-- 協議トピック
CREATE TABLE public.deliberation_topics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'OPEN', -- 'OPEN', 'RESOLVED', 'ARCHIVED'
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 投票データ (Quadratic Voting / 二次投票に対応)
CREATE TABLE public.deliberation_votes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  topic_id UUID NOT NULL REFERENCES public.deliberation_topics(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  vote_weight INTEGER NOT NULL CHECK (vote_weight != 0), -- 二次投票での投票ポイント数
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(topic_id, user_id)
);

-- 協議コメント（意見クラスタリング用）
CREATE TABLE public.deliberation_comments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  topic_id UUID NOT NULL REFERENCES public.deliberation_topics(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  message TEXT NOT NULL,
  parent_id UUID REFERENCES public.deliberation_comments(id) ON DELETE CASCADE,
  opinion_cluster INTEGER, -- Pol.is的なベクトル分類クラスタ (1: 賛成派, 2: 反対派 等)
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 合意統計データ
CREATE TABLE public.deliberation_stats (
  topic_id UUID PRIMARY KEY REFERENCES public.deliberation_topics(id) ON DELETE CASCADE,
  total_voters INTEGER NOT NULL DEFAULT 0,
  agreement_rate REAL NOT NULL DEFAULT 0.0, -- 合意率 (0.0 - 1.0)
  consensus_summary TEXT, -- AIによる主要な合意ポイント要約
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 1.4 クラウドファンディング & プロジェクト実行（volunteer-connectより）

```sql
-- クラファンキャンペーン
CREATE TYPE public.crowdfunding_status AS ENUM (
  'DRAFT',
  'ACTIVE',
  'FUNDED',
  'CLOSED'
);

CREATE TABLE public.crowdfunding_campaigns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organizer_user_id UUID REFERENCES public.profiles (id) ON DELETE SET NULL,
  npo_profile_id UUID REFERENCES public.npo_profiles (id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  organizer_name TEXT NOT NULL,
  category TEXT NOT NULL,
  image_url TEXT,
  goal_amount BIGINT NOT NULL CHECK (goal_amount > 0),
  current_amount BIGINT NOT NULL DEFAULT 0,
  donor_count INTEGER NOT NULL DEFAULT 0,
  deadline DATE NOT NULL,
  status public.crowdfunding_status NOT NULL DEFAULT 'ACTIVE',
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);

-- リターン（支援段階）
CREATE TABLE public.crowdfunding_rewards (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id UUID NOT NULL REFERENCES public.crowdfunding_campaigns (id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  min_amount INTEGER NOT NULL CHECK (min_amount >= 0),
  available_count INTEGER
);

-- クラファン寄付履歴
CREATE TABLE public.crowdfunding_donations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id UUID NOT NULL REFERENCES public.crowdfunding_campaigns (id) ON DELETE CASCADE,
  donor_user_id UUID REFERENCES public.profiles (id) ON DELETE SET NULL,
  donor_name TEXT NOT NULL,
  amount INTEGER NOT NULL CHECK (amount > 0),
  message TEXT,
  stripe_session_id TEXT UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);

-- ボランティアプロジェクト（実行案件）
CREATE TYPE public.project_status AS ENUM (
  'DRAFT',
  'NEGOTIATING',
  'OPEN',
  'ACTIVE',
  'COMPLETED'
);

CREATE TABLE public.projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  npo_profile_id UUID REFERENCES public.npo_profiles (id) ON DELETE SET NULL,
  created_by UUID REFERENCES public.profiles (id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  npo_name TEXT NOT NULL,
  event_date DATE NOT NULL,
  duration_hours INTEGER NOT NULL CHECK (duration_hours > 0),
  location TEXT NOT NULL,
  category TEXT NOT NULL,
  description TEXT NOT NULL,
  impact_goal TEXT NOT NULL,
  status public.project_status NOT NULL DEFAULT 'DRAFT',
  participants INTEGER NOT NULL DEFAULT 0,
  max_participants INTEGER NOT NULL DEFAULT 1,
  embedding vector(4096), -- プロジェクト要求・目的ベクトル (Modal用)
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);

-- ボランティア応募
CREATE TABLE public.project_applications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES public.projects (id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES public.profiles (id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'APPROVED', 'REJECTED'
  applied_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  UNIQUE (project_id, user_id)
);
```

---

## 2. API エンドポイントおよびRPC仕様

### 2.1 クラウドファンディング寄付処理 (RPC)
寄付発生時にトランザクション管理下でキャンペーンの調達額・支援者数をアトミックに加算します。
```sql
CREATE OR REPLACE FUNCTION public.donate_to_campaign(
  p_campaign_id UUID,
  p_donor_name TEXT,
  p_amount INTEGER,
  p_message TEXT,
  p_donor_id UUID,
  p_stripe_session_id TEXT
)
RETURNS JSONB AS $$
DECLARE
  v_donation_id UUID;
  v_campaign_record RECORD;
BEGIN
  -- 寄付レコードの挿入
  INSERT INTO public.crowdfunding_donations (campaign_id, donor_user_id, donor_name, amount, message, stripe_session_id)
  VALUES (p_campaign_id, p_donor_id, p_donor_name, p_amount, p_message, p_stripe_session_id)
  RETURNING id INTO v_donation_id;

  -- キャンペーン金額・人数の更新
  UPDATE public.crowdfunding_campaigns
  SET current_amount = current_amount + p_amount,
      donor_count = donor_count + 1
  WHERE id = p_campaign_id
  RETURNING * INTO v_campaign_record;

  RETURN jsonb_build_object(
    'success', true,
    'donation_id', v_donation_id,
    'campaign', to_jsonb(v_campaign_record)
  );
END;
$$ LANGUAGE plpgsql;
```

---

## 3. スケジュール実行 (Cron) の更新

| ジョブ名 | 実行頻度 | cron式 | 説明 | パッケージパス |
|---|---|---|---|---|
| 富山市RSS収集 | 日次 02:00 JST | `0 2 * * *` | 富山市公式RSSフィードのパース・新規補助金追加 | `collectors/public/diff_toyama_city.py` |
| 富山県ページ差分 | 日次 02:30 JST | `30 2 * * *` | 富山県新着ページのハッシュ比較とLLMによる構造化 | `collectors/public/diff_toyama_pref.py` |
| jGrants同期 | 日次 03:00 JST | `0 3 * * *` | jGrants WebAPIからの全国補助金情報の同期取得 | `collectors/public/jgrants_sync.py` |
| 予算データ収集 | 週次 月曜03:30 JST | `30 3 * * 1` | `collectors/budget_fetch.py` | - |
| 交付金カスケード | 週次 月曜04:00 JST | `0 4 * * 1` | `collectors/cascade_watch.py` | - |
| 民間助成金収集 | 週次 日曜05:00 JST | `0 5 * * 0` | 民間助成財団の公募情報の収集・LLM構造化 | `collectors/private/private_grant_fetcher.py` |

---

## 4. ボランティア・スキルマッチング仕様 (RAG基盤流用)

助成金RAGで使用している Modal GPU インフラ（Qwen3-Embedding-8B + BgeReranker v2-m3）と Supabase ベクトルストアを再利用し、ボランティアとプロジェクトのスキルマッチングを行います。

### 4.1 ベクトル生成データソースマッピング

* **プロジェクト要求ベクトル (`projects.embedding`)**:
  * **ソース**: プロジェクトの `title`、`description`（活動詳細）、`category`、`impact_goal`、および `needed_resources`（必要なスキルアセット）を結合したテキスト。
* **ユーザープロフィールベクトル (`profiles.embedding`)**:
  * **ソース**: ユーザーの `display_name`、`bio`（自己紹介）、`interest_areas`、および `members` や `project_applications` (過去の完了活動実績) から動的に構築した活動経歴サマリーテキスト。

### 4.2 マッチングアルゴリズム
1. **SQL事前フィルタ (一次絞り込み)**:
   * スケジュール（`event_date` が未来）、地域（`location` が一致またはリモート可）、募集人数（`participants < max_participants`）に合致するプロジェクトに絞り込み。
2. **コサイン類似度検索 (二次絞り込み)**:
   * `profiles.embedding` と `projects.embedding` のコサイン類似度を pgvector を使って計算。上位10件を抽出。
3. **リランキング (最終スコアリング)**:
   * Modal上の `BgeReranker v2-m3` に、ユーザーの活動経歴テキストとプロジェクト概要テキストのペアを送信し、クロスエンコーダによる最終類似度 `match_score` (0.0 - 1.0) を算出。
4. **説明テキストの自動生成 (Explainable Matching)**:
   * スコア上位の案件に対し、LLM (Gemini/Claude) を用いて「なぜあなたにこの案件がおすすめなのか」のパーソナライズされた推薦理由（2〜3行）を自動生成。

### 4.3 API エンドポイント仕様

#### `GET /api/matches/projects` (おすすめプロジェクト一覧)
* **認証**: 必要 (Bearer Token)
* **クエリパラメータ**: `limit` (デフォルト 10)
* **レスポンス**:
  ```json
  [
    {
      "project_id": "uuid-xxx",
      "title": "富山市こどもプログラミング教室サポート",
      "npo_name": "Open Coral Network",
      "match_score": 0.92,
      "match_reason": "あなたは過去に『IT支援ボランティア』の経験があり、このプロジェクトが必要とするプログラミング指導補助の要件に合致しています。"
    }
  ]
  ```

#### `GET /api/flows/connections` (2D 政策・助成金接続フローデータ)
* **概要**: 自治体総合計画から実行プロジェクトまでの縦方向の接続パス、および将来フェーズ用の横方向（同階層）の関連度データを取得します。
* **認証**: 必要 (Bearer Token)
* **レスポンス**:
  ```json
  {
    "nodes": [
      { "id": "vision-1", "level": 1, "label": "コンパクトシティの推進", "type": "vision" },
      { "id": "policy-1-2", "level": 2, "label": "公共交通の活性化", "type": "policy" },
      { "id": "subsidy-101", "level": 3, "label": "地域交通活性化補助金", "type": "subsidy" },
      { "id": "project-201", "level": 4, "label": "車いすスマートモビリティ運行", "type": "project" }
    ],
    "links": [
      { "source": "vision-1", "target": "policy-1-2", "direction": "vertical", "weight": 1.0 },
      { "source": "policy-1-2", "target": "subsidy-101", "direction": "vertical", "weight": 1.0 },
      { "source": "subsidy-101", "target": "project-201", "direction": "vertical", "weight": 0.85 },
      
      /* 将来フェーズ(Phase 2)用: 同階層の横方向関連度 (コサイン類似度等) */
      { "source": "subsidy-101", "target": "subsidy-102", "direction": "horizontal", "weight": 0.68 }
    ]
  }
  ```

---

## 5. 発発展的・将来フェーズ仕様 (Phase 3)

### 5.1 採択シミュレーター (RFP Compliance Check)
* **概要**: AIエージェント群が自治体側の審査官として動作し、作成された提案書（GovPro）が公募仕様書（RFP）の要求を満たしているかをプレ評価します。
* **APIエンドポイント**: `POST /api/proposals/simulate`
  * **リクエスト**:
    ```json
    {
      "proposal_text": "# 提案書本文...",
      "rfp_document_id": "uuid-docs-123"
    }
    ```
  * **バックエンドロジック**: 
    1. RFPドキュメントから評価項目・配点（例: 実現可能性 30点、地域課題適合度 30点、予算効率性 40点）を自動抽出。
    2. LLMに「財務審査員」「法務審査員」「地域住民代表」の各プロンプトを与えて並列レビューを実行。
    3. 合計スコアおよび「どの項目を改善すれば加点されるか」の推敲アドバイスを返却。

### 5.2 自己主権型デジタル実績証明 (DID & ZKP / Verifiable Credentials)
* **概要**: W3C標準の検証可能資格（VC）および zk-SNARKs を用いて、ユーザーが個人情報を開示せずに、住民権や活動実績を証明する仕様。

```sql
-- 信頼できる発行者 (Trusted Issuers) ホワイトリスト
CREATE TABLE public.trusted_issuers (
  did TEXT PRIMARY KEY,                         -- 発行者のDID
  name TEXT NOT NULL,                           -- 発行者名 (自治体名、NPO法人名など)
  is_active BOOLEAN NOT NULL DEFAULT true,      -- アクティブ状態
  created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ DEFAULT TIMEZONE('utc', NOW())
);

-- ZKP対応 検証可能資格 (Verifiable Credentials) ストア
CREATE TABLE public.zk_verifiable_credentials (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  issuer_did TEXT NOT NULL REFERENCES public.trusted_issuers (did) ON DELETE RESTRICT, -- 発行者DID (ホワイトリスト制限)
  holder_id UUID NOT NULL REFERENCES public.profiles (id) ON DELETE CASCADE,           -- DB内部リレーション用
  holder_did TEXT NOT NULL,                                                            -- VC仕様準拠のホルダーDID（非FK、暗号検証用）
  credential_type TEXT NOT NULL,                                                       -- 'VOLUNTEER_HOURS', 'RESIDENT_PROOF'
  
  -- 生の実績・属性データを暗号学的にコミットして隠蔽したハッシュ
  commitment_hash TEXT NOT NULL UNIQUE,
  
  -- クライアント側 (WASM) で生成された zk-SNARKs (Groth16等) の証明データ
  zk_proof JSONB NOT NULL,
  
  -- 資格の有効性フラグ (DIDローテーションや明示的な失効処理でfalseに設定される)
  is_valid BOOLEAN NOT NULL DEFAULT true,
  
  created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc', NOW())
);

-- 資格失効リスト (DIDローテーションや鍵紛失時の無効化管理用)
CREATE TABLE public.revocation_list (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  credential_id UUID REFERENCES public.zk_verifiable_credentials (id) ON DELETE CASCADE,
  revoked_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  reason TEXT
);

-- RLS (Row Level Security) ポリシー設定
ALTER TABLE public.zk_verifiable_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.revocation_list ENABLE ROW LEVEL SECURITY;

-- ホルダー本人は自身のVCを閲覧・削除可能
CREATE POLICY zk_vc_holder_policy ON public.zk_verifiable_credentials
  FOR ALL
  USING (holder_id = auth.uid());

-- 検証者は検証用に有効な証明のみ読み込み可能
CREATE POLICY zk_vc_verifier_policy ON public.zk_verifiable_credentials
  FOR SELECT
  USING (is_valid = true);

-- 失効リストの読み取りポリシー
CREATE POLICY revocation_list_read_policy ON public.revocation_list
  FOR SELECT
  USING (true);
```

#### 5.2.1 zk_proof スキーマと検証仕様
* **zk_proof カラム期待スキーマ (Groth16 形式)**:
  `zk_proof` カラムに格納される JSONB データは、以下のスキーマを満たす必要があります。
  ```json
  {
    "pi_a": ["string", "string", "string"],
    "pi_b": [
      ["string", "string"],
      ["string", "string"],
      ["string", "string"]
    ],
    "pi_c": ["string", "string", "string"],
    "public_signals": ["string"]
  }
  ```
  * **データサイズ上限**: 10KB（アプリケーションレイヤーでのバリデーションで強制）。
  * **使用ツール/プロトコル**: 回路記述は `Circom`、証明システムは `Groth16` (snarkjs) を使用。バックエンドの検証エンジン（Node.js / FastAPI）に事前にデプロイした `verification_key.json` をロードし、`snarkjs.groth16.verify` にて暗号学的な整合性を検証する。

#### 5.2.2 DID ローテーションと移行ポリシー
1. **DID ローテーションの定義**: ホルダーがセキュリティ等の理由で自身の公開鍵を変更し、新しい DID（`profiles.did`）を生成すること。
2. **VC 移行ポリシーと無効化**: 
   * `zk_verifiable_credentials` レコードは `holder_id` (UUID) により `profiles` と紐づいているため、DIDの変更によってレコード自体が自動的に削除されることはありません。
   * しかし、古い DID 署名に基づいた VC はセキュリティリスクとなるため、ローテーション完了時に、古い `holder_did` を持つすべての既存 VC レコードは `is_valid = false` に自動的に更新（失効）され、同時に `revocation_list` テーブルに失効記録（理由: 'DID_ROTATION'）が挿入されます。
   * 失効した VC は検証者から参照できなくなります（RLSポリシーによりブロック）。
   * ユーザーは、新しい DID に紐づく新規 VC の再発行（Re-issuance）を発行者（Issuer）にリクエストし、新しい ZKP 証明を生成して登録する必要があります。

#### 5.2.3 旧 `digital_badges` テーブルからのマイグレーション計画
テーブル名の変更（`digital_badges` → `zk_verifiable_credentials`）は破壊的変更であるため、以下の移行手順を実施します。
1. **依存コードの改修**: 既存の `digital_badges` を参照しているフロントエンド UI（実績一覧）および API エンドポイントを、`zk_verifiable_credentials` を参照する仕様に変更します。
2. **移行用 SQL スクリプトの実行**:
   * 新しい `trusted_issuers` テーブルおよび `zk_verifiable_credentials` テーブルを作成。
   * プラットフォーム自体のシステム DID を初期値として `trusted_issuers` に登録。
   * 既存の `digital_badges` のレコードを移行するため、`user_id` を `holder_id` にマッピングし、適当なプレースホルダー `holder_did` (ユーザーがDID未生成の場合はダミーDID `did:key:...`)、およびダミーの `commitment_hash` と `zk_proof` (ダミー証明データ) を作成してデータをインポート。
   * インポート完了後、旧 `digital_badges` テーブルを削除。

### 5.3 インパクト・フィードバックループ (Impact Measurement)
* **概要**: 実行したプロジェクトが地域社会に与えたアウトカム指標をデータベースに格納し、予算フロー画面の終端ノード（`nodes` / `edges`）に動的に紐づけて表示します。

```sql
-- 社会的インパクト実績テーブル
CREATE TABLE public.project_impacts (
  id SERIAL PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES public.projects (id) ON DELETE CASCADE,
  metric_name TEXT NOT NULL,                    -- 指標名 (例: '子供向け学習支援の実施回数', '車いす送迎回数')
  metric_value REAL NOT NULL,                   -- 実績数値
  metric_unit TEXT NOT NULL,                    -- 単位
  impact_summary TEXT,                          -- AIによる社会的価値の要約
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```
* **フローへの統合方法**: 
  `GET /api/flows/connections` 実行時、プロジェクトノード（Level 4）に `project_impacts` のデータを結合してマージし、UI上で「この資金（¥1,500,000）によって【車いす送迎120回】が実現した」という成果テキストをポップオーバーで表示します。

