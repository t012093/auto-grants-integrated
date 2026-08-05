# 助成金・企画書共有プラットフォーム 最終決定版マスター仕様書 (Grant Proposal Collaboration Master Spec)

## 1. システム概要・目的

本システムは、NPO・社会活動団体が助成金・補助金に応募する前のアイデア構想段階から企画書を作成し、`auto-grants-integrated`（AI解析・生成思考コア）と `ai-note-meet`（プロジェクト管理・チーム連携・Fumadocs Wiki表示エンジン）を連携させ、オファー発行・先着エントリー・ポジション決定・タスク/カレンダー/プロジェクト管理・Hyperframesプレゼン生成・採択後フォロー・監査エビデンス保管までを一元管理する統合プラットフォームである。

---

## 2. 理想的な全体ワークフロー (Step 1 〜 Step 7)

```mermaid
graph TD
    classDef step fill:#e1f5fe,stroke:#0288d1,stroke-width:2px;
    classDef team fill:#fff9c4,stroke:#fbc02d,stroke-width:2px;
    classDef output fill:#e8f5e9,stroke:#388e3c,stroke-width:2px;
    classDef audit fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px;

    Sub1[Step 1: 多系データソース抽出 & AI適合判定<br>jGrants / e-Rad / 民間財団] :::step --> Sub2[Step 2: 企画書ルールに基づく構想・RAG作成] :::step
    Sub2 --> Sub3[Step 3: スキル: task_human_ai_allocator にて<br>タスクを AUTO / HYBRID / HUMAN にDAG分解] :::step
    Sub3 --> Sub4[Step 4: ai-note-meet でオファー投稿 &<br>先着順エントリーでメンバーポジション確定] :::team
    Sub4 --> Sub5[Step 5: Fumadocsペラ1サマリーWiki同期 &<br>Hyperframes プレゼン資料自動生成] :::output
    Sub5 --> Sub6[Step 6: 富山市等の窓口事前相談 & 正式申請提出] :::step
    Sub6 --> Sub7[Step 7: 採択後概算払い請求 & 監査エビデンス保管] :::audit

    Sub5 --> Out1[ai-note-meet 内 Fumadocs Wiki<br>トップ: ペラ1サマリー / 配下: 詳細企画書] :::output
    Sub5 --> Out2[Hyperframes プレゼンPDF / PPTX / Webスライド] :::output
    Sub5 --> Out3[Word 申請書類 .docx / 概算払い様式] :::output
```

---

## 3. `auto-grants` ⇔ `ai-note-meet` 役割分担 ＆ データ連携仕様

### 3.1 役割分担の原則 (Separation of Concerns)
- **`auto-grants-integrated` (思考・データ解析・生成コア)**:
  - jGrants/e-Rad等のデータ抽出・17項目適合判定 (`check_eligibility.py`)
  - 過去採択事例の分析 ＆ BGE-M3 (1024次元) ベクトル検索
  - 企画書本文・使途500万円予算JSONの自動生成
  - `skills/task_human_ai_allocator` によるタスクの DAG 分解・属性ラベル付与 (`AUTO` / `HYBRID` / `HUMAN`)
  - 提出用 Word (`.docx`) および **Hyperframes** プレゼンコードのレンダリング
- **`ai-note-meet` (実行・チーム連携・Fumadocs Wiki表示レイヤー)**:
  - **Fumadocs** をフロントエンドUIとして組み込み、Wikiページ（トップ: ペラ1サマリー）を表示
  - アナウンス投稿 (`create_announcement`) によるプロジェクトオファー＆先着エントリー受付
  - メンバーポジション確定とアクセス権限付与 (`set_project_member_permissions`)
  - ポジション別初期タスクの自動アサイン (`create_task`) と共有カレンダー登録 (`create_calendar_entry`)
  - ページ単位の外部公開 (`publish_page`) による農家・行政との共有
  - 採択後の概算払い・タイムシートリマインダー自動化 (`create_automation_job`)

### 3.2 同期メカニズム — `sync_proposal_to_ai_note_meet.py` は「MCP計画生成器」(実呼び出しは Agent)

- **役割**: `scripts/sync_proposal_to_ai_note_meet.py` は ai-note-meet への同期を**直接 HTTP で実行しない**。
  企画書データ(`grant_proposals`` + `proposal_grant_mappings`` + `proposal_project_offers`)を DB から読み、
  「どの `mcp__ai_note_meet__*` ツールを、どの順番で、どの引数で呼ぶか」を**順序付き計画(JSON)** として生成する。
  実際の同期呼び出しは **Agent (Antigravity/Hermes 等) が MCP ツールで実行**する。
- **理由**: ai-note-meet へのアクセスは MCP により提供されており、外部HTTP APIの仕様を本リポジトリから
  推測せず、環境と一貫させるため(HTTPクライアントの重複実装・未定義のAPIキー設定を避ける)。
- **計画の末尾 (write_back)**: 計画の最終ステップ `update_proposal_ai_note_ids` が、実行者に対し
  `create_project` / `create_page` の返り値IDで `grant_proposals.ai_note_project_id` / `ai_note_page_id` を
  UPDATE すると指示する。これにより次回以降が冪等(早期スキップ)になる。
- **冪等性**: `ai_note_project_id` が既に設定済みの企画書は、`sync()` 冒頭で再度の計画生成を
  スキップし `__already_synced` (kind=info) の計画のみ返す。
- **CLI**: `--json`(計画を機械可読で出力)を任意指定。本スクリプトは**常に**実MCPを呼ばず計画のみを生成する
  (ドライラン用フラグは不要。実同期は Agent が `mcp__ai_note_meet__*` を実行)。

### 3.2.1 計画 JSON スキーマ($ref 依存連鎖)

各ステップは以下の構造を持つ:

| フィールド | 型 | 説明 |
|---|---|---|
| `step` | int | 1始まりの実行順 |
| `kind` | str | `mcp`(MCP呼び出し) / `info`(スキップ) / `write_back`(実行後DB更新) |
| `tool` | str | `mcp__ai_note_meet__*` のツール名(または特殊名) |
| `args` | object | 呼び出し引数 |
| `deps` | int[] | 依存する先行 step 番号(任意) |
| `key` / `when` / `note` | str | 実行者への契約・解決指示(任意) |

`args` 内の値が `{"$ref": "#/steps/<N>/result.<field>"}` のとき、実行者は step N の MCP 返り値の
`<field>`(例 `project_id` / `page_id`)を代入してから呼び出す。これにより `create_project` の返り値
`project_id` が後続の `create_page` / `create_calendar_entry` / `create_task` へ確実に受け継がれ、
孤立レコードや task 作成失敗(-32602)を防ぐ。

**例 (ペラ1 + 詳細 + 締切カレンダー):**

```json
[
  {"step":1,"kind":"mcp","tool":"create_project","args":{"name":"…","description":"…"}},
  {"step":2,"kind":"mcp","tool":"create_page","args":{"title":"🏠 …(ペラ1)","content":"…","project_id":{"$ref":"#/steps/1/result.project_id"}},"deps":[1]},
  {"step":3,"kind":"mcp","tool":"create_page","args":{"title":"📖 詳細企画書","content":"…","project_id":{"$ref":"#/steps/1/result.project_id"},"parent_id":{"$ref":"#/steps/2/result.page_id"}},"deps":[1,2]},
  {"step":4,"kind":"mcp","tool":"create_calendar_entry","args":{"title":"【締切】…","description":"…","entry_category":"助成金締切","date":"2026-08-31","project_id":{"$ref":"#/steps/1/result.project_id"}},"deps":[1]},
  {"step":5,"kind":"write_back","tool":"update_proposal_ai_note_ids","args":{"proposal_id":"…","ai_note_project_id":{"$ref":"#/steps/1/result.project_id"},"ai_note_page_id":{"$ref":"#/steps/2/result.page_id"}},"deps":[1,2],"when":"create_project / create_page(ペラ1) 成功後"}
]
```

### 3.2.2 MCP 実契約との整合 (2026-08-05 確認)

生成される計画は ai-note-meet の MCP 実契約(`backend/mcp/tools/*.py`)に従う:

| ツール | 必須引数 | 計画での扱い |
|---|---|---|
| `create_project` | `name` | ✅ そのまま |
| `create_page` | `title`+ / `project_id`・`parent_id`(任意) | `project_id` を Step1 の返り値で設定。詳細は `parent_id` でペラ1配下 |
| `create_calendar_entry` | `title`+ / `entry_category`+ | `entry_category="助成金締切"`, `date`=助成金 `deadline` |
| `create_announcement` | `title`+ / `description`+ | `description` キーを使用(`content` ではない) |
| `create_task` | `project_id`+ / `title`+ | `project_id` を Step1 の返り値で設定 |

**ページIDの保持方針**: `grant_proposals.ai_note_page_id` は**ペラ1(Step2)ページのIDのみ**を保持する。
詳細企画書ページはペラ1の子ページ(`parent_id`)として project 配下に存在し、個別IDは管理しない。

---

## 4. 人間 ✕ AI タスク自動分離ルール (`skills/task_human_ai_allocator`)

| 属性タグ | 性質 | 具体例 | 担当 |
|---|---|---|---|
| **`AUTO`** | データのパース・計算・生成・ログ保存 | jGrantsスキャン、適合判定、予算500万計算、Hyperframesコード生成 | **AI (AutoGrants)** |
| **`HYBRID`** | 生成物の最終チェック・承認・ボタン操作 | 企画書本文の最終チェック、SNS投稿文の承認、申請ボタンの押下 | **人間 (PM / 担当者)** |
| **`HUMAN`** | 信頼構築・リアル対話・現場対応・対面 | **富山市役所窓口での対面事前相談**、**農家さんとの対話・同意書印字**、**子ども食堂現場運営** | **人間メンバー (先着)** |

---

## 5. Fumadocs Wiki の UI ＆ サイドバー構成仕様

`ai-note-meet` 上のプロジェクトWiki（Fumadocs）は、**トップ画面に「30秒でわかるペラ1サマリー」を常時デフォルト表示** し、1クリックで詳細Wikiへ遷移できる構造とする。

```
┌───────────────────────────────────────────────┐
│ 🏠 プロジェクト概要 (ペラ1)  👈 【デフォルト表示】│
├───────────────────────────────────────────────┤
│ ▼ 📖 詳細企画書                               │
│    ├ 1. 地域の課題と解決ストーリー             │
│    ├ 2. コア事業内容 (子ども食堂×AI×EC)      │
│    ├ 3. 500万円予算使途明細                   │
│    └ 4. 実施マイルストーン                    │
├───────────────────────────────────────────────┤
│ 👥 チーム・先着ポジション募集                 │
├───────────────────────────────────────────────┤
│ 📄 提出書類 & 協議会同意書                    │
└───────────────────────────────────────────────┘
```

---

## 6. データベースアーキテクチャ (Neon PostgreSQL + pgvector)

```sql
-- 1. 企画書マスターテーブル
CREATE TABLE IF NOT EXISTS public.grant_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    npo_id UUID NOT NULL REFERENCES public.npo_profiles(id) ON DELETE CASCADE,
    
    title VARCHAR(255) NOT NULL,
    concept_summary TEXT, -- ペラ1概要テキスト
    
    ai_note_project_id VARCHAR(100), -- ai-note-meet プロジェクトID
    ai_note_page_id VARCHAR(100), -- ai-note-meet Wiki「ペラ1」ページID(詳細企画書はペラ1の子ページ)
    
    status VARCHAR(50) DEFAULT 'IDEA', 
    -- IDEA, DRAFT, IN_REVIEW, PARTNER_MATCHING, READY, SUBMITTED, ADOPTED, REJECTED, COMPLETED
    
    content_markdown TEXT, -- 詳細企画書Markdown
    budget_json JSONB, -- 500万円使途内訳
    kpi_json JSONB,
    
    embedding vector(1024), -- BGE-M3 (過去ナレッジ検索用)
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. 助成金多対多マッピング
CREATE TABLE IF NOT EXISTS public.proposal_grant_mappings (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,
    grant_id BIGINT NOT NULL REFERENCES public.grants(id) ON DELETE CASCADE,
    
    is_primary BOOLEAN DEFAULT FALSE,
    match_score INT,
    status VARCHAR(50) DEFAULT 'CONSIDERING',
    notes TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(proposal_id, grant_id)
);

-- 3. プロジェクトオファー＆ポジション管理
CREATE TABLE IF NOT EXISTS public.proposal_project_offers (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,
    
    position_name VARCHAR(100) NOT NULL,
    capacity INT DEFAULT 1,
    compensation_notes TEXT,
    status VARCHAR(50) DEFAULT 'RECRUITING',
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. メンバー先着エントリー管理
CREATE TABLE IF NOT EXISTS public.proposal_offer_entries (
    id BIGSERIAL PRIMARY KEY,
    offer_id BIGINT NOT NULL REFERENCES public.proposal_project_offers(id) ON DELETE CASCADE,
    
    applicant_name VARCHAR(255) NOT NULL,
    applicant_email VARCHAR(255),
    entry_order INT NOT NULL,
    status VARCHAR(50) DEFAULT 'ACCEPTED',
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. 窓口コミュニケーション履歴
CREATE TABLE IF NOT EXISTS public.proposal_communications (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,
    
    contact_target VARCHAR(255) NOT NULL,
    channel VARCHAR(50) DEFAULT 'EMAIL',
    summary TEXT NOT NULL,
    full_log TEXT,
    next_action_date DATE,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. 二重申請防止・リソース重複監視テーブル
CREATE TABLE IF NOT EXISTS public.proposal_resource_allocations (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,
    
    resource_type VARCHAR(50) NOT NULL,
    resource_identifier VARCHAR(255) NOT NULL,
    allocated_percentage INT DEFAULT 100,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. 監査エビデンス一括保管テーブル
CREATE TABLE IF NOT EXISTS public.proposal_audit_evidences (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,
    
    evidence_type VARCHAR(50) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_path VARCHAR(512) NOT NULL,
    verification_status VARCHAR(50) DEFAULT 'VERIFIED',
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. 不採択分析 ＆ ピボット再利用管理テーブル
CREATE TABLE IF NOT EXISTS public.proposal_reviews_and_retries (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,
    
    rejection_reason_category VARCHAR(100),
    feedback_notes TEXT,
    pivot_target_grant_id BIGINT REFERENCES public.grants(id) ON DELETE SET NULL,
    pivot_status VARCHAR(50) DEFAULT 'ANALYZED',
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 7. まとめ

自前での余計なセキュリティ開発（`share_token` 等）を排除し、アクセス制御・公開は `ai-note-meet` のネイティブ機能へ完全移譲。さらに Fumadocs Wiki の1ページ目に「ペラ1サマリー」を組み込むことで、**人間にとってもAIにとっても最も美しい全自動プラットフォーム仕様** が完成した。
