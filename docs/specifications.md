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
  organization_kind public.entity_kind NOT NULL,
  organization_id UUID NOT NULL, -- company_profiles または npo_profiles のID
  user_id UUID REFERENCES public.profiles (id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  role public.app_user_role NOT NULL,
  department TEXT,
  avatar_url TEXT,
  bio TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);
```

### 1.2 助成金・予算・自治体RAG（auto-grantsv2 & moneyflow-visualizer）

```sql
-- 助成金データ本体
CREATE TABLE public.grants (
  id SERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  provider TEXT NOT NULL,
  amount_max BIGINT,
  deadline DATE,
  details_url TEXT,
  payload_json JSONB NOT NULL DEFAULT '{}'::JSONB,
  cascade_id INTEGER, -- 交付金カスケードIDへの参照 (Nullable)
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_grants_payload_data_hash ON public.grants ((payload_json->>'data_hash'));
CREATE INDEX idx_grants_cascade_id ON public.grants(cascade_id);

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
  type TEXT NOT NULL,                    -- 'country', 'ministry', 'subsidy', 'recipient_org' 等
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
  data_format TEXT,                      -- 形式 (API/JSON, PDF, Excel等)
  fiscal_years TEXT,                     -- 対象年度範囲
  created_at TIMESTAMPTZ DEFAULT NOW()
);
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
  p_donor_id UUID
)
RETURNS JSONB AS $$
DECLARE
  v_donation_id UUID;
  v_campaign_record RECORD;
BEGIN
  -- 寄付レコードの挿入
  INSERT INTO public.crowdfunding_donations (campaign_id, donor_user_id, donor_name, amount, message)
  VALUES (p_campaign_id, p_donor_id, p_donor_name, p_amount, p_message)
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

| ジョブ名 | 実行頻度 | cron式 | 説明 |
|---|---|---|---|
| 富山市RSS収集 | 日次 02:00 JST | `0 2 * * *` | 富山市公式RSSフィードのパース・新規補助金追加 |
| 富山県ページ差分 | 日次 02:30 JST | `30 2 * * *` | 富山県新着ページのハッシュ比較とLLMによる構造化 |
| jGrants同期 | 日次 03:00 JST | `0 3 * * *` | jGrants WebAPIからの全国補助金情報の同期取得 |
| 予算データ収集 | 週次 月曜03:30 JST | `30 3 * * 1` | `collectors/budget_fetch.py` |
| 交付金カスケード | 週次 月曜04:00 JST | `0 4 * * 1` | `collectors/cascade_watch.py` |

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
