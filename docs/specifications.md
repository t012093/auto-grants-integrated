# auto-grants-integrated 仕様書

> **Version**: 1.3 (Revised)  
> **更新日**: 2026-07-15  
> **ステータス**: Draft

---

## 1. データベース設計

### 1.1 既存テーブル（auto-grantsv2から継承）

(前バージョンと同様のため省略。grantsの引数定義等は維持。)

### 1.2 新規テーブル（Subsidy Radarから移植・拡張）

#### budget_calendar（国の予算カレンダー）
#### grant_cascade（交付金カスケード追跡）
#### collector_runs（コレクター実行ログ）

(前バージョンと同様のDDLを維持。)

### 1.3 既存テーブルの拡張およびリレーション

```sql
-- grants テーブルへの外部キー追加
ALTER TABLE grants
    ADD COLUMN cascade_id INTEGER REFERENCES grant_cascade(id) ON DELETE SET NULL;

CREATE INDEX idx_grants_cascade_id ON grants(cascade_id);

-- 重複排除の高速化：JSON内のdata_hashに対する関数インデックス
CREATE INDEX idx_grants_payload_data_hash ON grants ((payload_json->>'data_hash'));

-- embedding カラムの型を 4096 次元 (Modal) に拡張して固定
ALTER TABLE knowledge_chunks
    ALTER COLUMN embedding TYPE vector(4096);

-- HNSW インデックスの作成（コサイン類似度用）
DROP INDEX IF EXISTS idx_knowledge_chunks_embedding_hnsw;
CREATE INDEX idx_knowledge_chunks_embedding_hnsw
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
```

### 1.4 予算フローグラフテーブル（moneyflow-visualizerから移植・拡張）

#### nodes（予算フローエンティティ）

```sql
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,                   -- 一意ID (例: 'JPN', 'MIN_CFA', 'SUB_CHILD_CARE')
    name TEXT NOT NULL,                    -- 日本語表示名
    name_en TEXT,                          -- 英語名
    lat REAL,                              -- 緯度 (Globe表示用)
    lng REAL,                              -- 経度 (Globe表示用)
    region TEXT,                           -- 地域/分類
    type TEXT NOT NULL,                    -- ノード種別 (下記参照)
    dataset TEXT,                          -- 所属データセット
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_nodes_type ON nodes(type);
```

**ノード種別 (`type`) の定義:**

| type | 説明 | 例 |
|---|---|---|
| `country` | 国家 | `JPN` (日本) |
| `mof_expenditure` | 国庫歳出 | `EXP_SOCIAL` (社会保障関係費) |
| `mof_revenue` | 国庫歳入 | `REV_TAX` (租税収入) |
| `ministry` | 省庁 | `MIN_CFA` (こども家庭庁) |
| `mhlw_benefit` | 社会保障制度 | `BEN_MEDICAL` (医療) |
| `prefecture` | 都道府県 | `PREF_TOYAMA` |
| `subsidy` | **助成金・補助金** (新設) | `SUB_CHILD_CARE` |
| `recipient_org` | **採択組織** (新設) | `ORG_OCN` |

#### edges（資金フロー）

```sql
CREATE TABLE edges (
    id SERIAL PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES nodes(id),
    target_id TEXT NOT NULL REFERENCES nodes(id),
    category TEXT NOT NULL,                -- フロー分類 (下記参照)
    sub_category TEXT,                     -- 詳細分類
    value_usd REAL,                        -- 金額 (USD)
    value_jpy REAL,                        -- 金額 (JPY)
    fiscal_year INTEGER,                   -- 会計年度
    confidence REAL DEFAULT 1.0,           -- 信頼度 (0.0-1.0)
    source_dataset TEXT,                   -- データ出典名
    grant_id INTEGER REFERENCES grants(id),-- 助成金との紐付け (nullable)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_edges_src_tgt_fy UNIQUE (source_id, target_id, fiscal_year)
);

CREATE INDEX idx_edges_source ON edges(source_id);
CREATE INDEX idx_edges_target ON edges(target_id);
CREATE INDEX idx_edges_category ON edges(category);
CREATE INDEX idx_edges_fiscal_year ON edges(fiscal_year);
```

**カテゴリ (`category`) の定義:**

| category | 説明 |
|---|---|
| `trade` | 貿易 |
| `bop` | 国際収支 |
| `remittance` | 送金/給付 |
| `welfare` | 社会保障 |
| `subsidy_grant` | **予算→助成金への紐付け** (新設) |
| `subsidy_award` | **助成金→採択組織への交付** (新設) |

#### data_sources（予算データソース登録）

```sql
CREATE TABLE data_sources (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,                    -- データソース名 (英語)
    name_ja TEXT,                          -- 日本語名
    url TEXT,                              -- URL
    description TEXT,                      -- 説明
    data_format TEXT,                      -- 形式 (API/JSON, PDF, Excel等)
    fiscal_years TEXT,                     -- 対象年度
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 2. Embeddingプロバイダ仕様

### 2.1 環境変数設定

開発環境・本番環境ともに `EMBEDDING_PROVIDER=modal`、次元数は `4096` 固定で統一する。

| 変数名 | Modal時 (開発/本番) | Mock時 (オフライン開発用) | 説明 |
|---|---|---|---|
| `EMBEDDING_PROVIDER` | `modal` | `mock` | プロバイダ選択 |
| `EMBEDDING_BASE_URL` | `https://<user>--jobflow-ai-services-fastapi-app.modal.run` | (不要) | APIエンドポイント |
| `EMBEDDING_API_KEY` | (Modal API Key) | (不要) | API認証キー |
| `EMBEDDING_DIMENSIONS` | `4096` | `4096` | ベクトル次元数 (4096固定) |
| `EMBEDDING_TIMEOUT_SECONDS` | `90` | `10` | タイムアウト |

> **Mock時の動作制約**: `MockEmbeddingService` は pgvector のコサイン類似度計算時のゼロ除算 (division by zero) エラーを防止するため、全要素が `0.0` のゼロベクトルではなく、極小のランダムなノイズを含む 4096 次元ベクトルを返却する。

---

## 3. コレクター仕様

### 3.1 富山市RSSコレクター (`rss_toyama_city`)
- 実行頻度: 日次 (深夜 02:00 JST)
- 重複排除: タイトルとURLから生成した一意な `hash_key` によるUpsert処理。

### 3.2 富山県ページ差分コレクター (`diff_toyama_pref`)
- 実行頻度: 日次 (深夜 02:30 JST)
- **前処理**: BeautifulSoupによるHTMLクレンジング (script/style/nav等の除去)。
- **差分検知**: クレンジング後HTMLのSHA-256ハッシュを前回実行ログ (`collector_runs.page_hash`) と比較。
- **重複排除 (Upsert) ロジック**:
  - LLM (Claude Haiku) が抽出した個々の助成金情報に対し、**「タイトル + 公募開始日 + 管轄自治体名」**から算出した一意な `data_hash` を生成する。
  - `grants` テーブル内の既存レコードと `data_hash` を突合し、存在しなければINSERT、存在すれば既存レコードをUPDATE（Upsert）することで重複登録を排除する。
- **サーキットブレーカー**: 差分量が前回の 80% を超える場合、エラーログと管理者通知を作成し処理を停止。

### 3.3 jGrants同期コレクター (`jgrants_sync`)
- 実行頻度: 日次 (深夜 03:00 JST)
- 重複排除: jGrants APIの `id` によるUpsert処理。

### 3.4 交付金カスケードウォッチャー (`cascade_watch`)
- 実行頻度: 週次 (毎週月曜 04:00 JST)
- **添付ファイル抽出機能**:
  - こども家庭庁等の交付金概要ページ内の PDF (`.pdf`) および Excel (`.xlsx`, `.xls`) リンクを自動検出する。
  - ダウンロードしたPDF/Excelファイルをテキスト化（PDFMiner、openpyxl等のライブラリを活用）し、構造化テキストに変換。
  - 抽出されたテキストを LLM (Claude) に入力し、「国の交付金 → 富山県の実施計画 → 富山市の事業計画」のつながりを解析し、`grant_cascade` テーブルへ保存。該当する `grants` がある場合は `cascade_id` に外部キーを紐付ける。

### 3.5 予算データフェッチャー (`budget_fetch`)
- 実行頻度: 週次 (毎週月曜 03:30 JST)
- **対象データソース**:
  - 財務省 一般会計予算 (PDF/Excel)
  - 厚生労働省 社会保障関係予算
  - こども家庭庁 予算概要 (PDF)
  - IMF DOTS / World Bank API
  - 日本銀行 資金循環統計
- **処理フロー**:
  1. 各公式サイトから最新の予算PDF/Excel/APIデータを取得。
  2. PDFMiner/openpyxl でテキスト抽出し、LLM (Claude) で省庁別・事業別の予算額を構造化抽出。
  3. 抽出結果を `nodes` (エンティティ) と `edges` (資金フロー) としてDBにUpsert。
  4. 助成金データ (`grants`) との紐付けが可能な場合、`edges.grant_id` に外部キーを設定。
- **信頼度 (`confidence`)**: 公式PDF直接引用の場合は `1.0`、LLM推計配分の場合は `0.7` を設定。

---

## 4. 収集スケジュール

ターゲットサイトの過負荷防止とレートリミット回避のため、バッチ処理を深夜のオフピーク時間帯に分散して実行する。

| 対象 | 実行頻度 | cron式 | 実行スクリプト |
|---|---|---|---|
| 富山市RSS | 日次 深夜02:00 | `0 2 * * *` | `collectors/rss_toyama_city.py` |
| 富山県ページ差分 | 日次 深夜02:30 | `30 2 * * *` | `collectors/diff_toyama_pref.py` |
| jGrants同期 | 日次 深夜03:00 | `0 3 * * *` | `collectors/jgrants_sync.py` |
| **予算データ収集** | **週次 月曜03:30** | `30 3 * * 1` | `collectors/budget_fetch.py` |
| 交付金カスケード | 週次 月曜04:00 | `0 4 * * 1` | `collectors/cascade_watch.py` |
| 締切リマインド | 日次 朝08:30 | `30 8 * * *` | `notify/deadline_reminder.py` |
| ミラサポ突合 | 月次 1日09:00 | `0 9 1 * *` | `collectors/mirasapo_crosscheck.py` |
