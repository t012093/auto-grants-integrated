# auto-grants-integrated 仕様書

> **Version**: 1.2 (Revised)  
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

---

## 4. 収集スケジュール

ターゲットサイトの過負荷防止とレートリミット回避のため、バッチ処理を深夜のオフピーク時間帯に分散して実行する。

| 対象 | 実行頻度 | cron式 | 実行スクリプト |
|---|---|---|---|
| 富山市RSS | 日次 深夜02:00 | `0 2 * * *` | `collectors/rss_toyama_city.py` |
| 富山県ページ差分 | 日次 深夜02:30 | `30 2 * * *` | `collectors/diff_toyama_pref.py` |
| jGrants同期 | 日次 深夜03:00 | `0 3 * * *` | `collectors/jgrants_sync.py` |
| 交付金カスケード | 週次 月曜04:00 | `0 4 * * 1` | `collectors/cascade_watch.py` |
| 締切リマインド | 日次 朝08:30 | `30 8 * * *` | `notify/deadline_reminder.py` |
| ミラサポ突合 | 月次 1日09:00 | `0 9 1 * *` | `collectors/mirasapo_crosscheck.py` |
