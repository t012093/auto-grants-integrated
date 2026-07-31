# スキル仕様書: 17項目要件適合チェッカー (check_eligibility.py)

## 1. 概要 & 目的
登録団体のプロファイル情報 (`public.npo_profiles` / `public.npo_knowledge_chunks`) と助成金の公募要件 (`public.grants` / `public.knowledge_chunks`) を受け取り、全 17 項目にわたる 3 段階判定（確定ルール 5 項目 / 書類突合 4 項目 / pgvectorセマンティック評価 & ルール 8 項目）で適合スコア (0-100%) と未準備書類差分を自動算出・`public.alerts` に保存する CLI スクリプト。

**特徴**:
- **外部 LLM API 不使用・完全ローカル/DB完結**
- **ハルシネーション 0%**: 全判定が確定ルール、集合演算、および `pgvector` コサイン類似度計算に基づく。
- **確定引用**: 適合根拠テキスト (`evidence_quote`) は DB 内の `knowledge_chunks.content` から直接抽出。

---

## 2. CLI インターフェース仕様

```bash
uv run skills/grant_eligibility_checker/scripts/check_eligibility.py \
  --org-id "<npo_profile_uuid>" \
  --grant-id "<grant_db_id_or_source_id>" \
  [--json]
```

### 引数
- `--org-id` (`str`, 必須): 判定対象の NPO 団体 UUID (`npo_profiles.id`)
- `--grant-id` (`str`, 必須): 判定対象の助成金 DB ID (`grants.id` または `grants.source_grant_id`)
- `--json` (`flag`, 任意): 結果を JSON フォーマットで標準出力

---

## 3. テーブル前提仕様 (`public.npo_knowledge_chunks`)

NPO 側のテキスト（活動分野・ターゲット層・団体概要）は、事前に `bge-base-ja-v1.5` (768次元) でベクトル化され、以下のテーブルに格納されている前提とする：

```sql
CREATE TABLE IF NOT EXISTS public.npo_knowledge_chunks (
  id SERIAL PRIMARY KEY,
  npo_profile_id UUID NOT NULL REFERENCES public.npo_profiles(id) ON DELETE CASCADE,
  chunk_type TEXT NOT NULL, -- 'ACTIVITY_TAGS', 'TARGET_AUDIENCE', 'DESCRIPTION'
  content TEXT NOT NULL,
  embedding vector(768) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT uq_npo_chunk_type UNIQUE (npo_profile_id, chunk_type)
);

CREATE INDEX IF NOT EXISTS idx_npo_knowledge_chunks_embedding_hnsw
ON public.npo_knowledge_chunks USING hnsw (embedding vector_cosine_ops);
```

---

## 4. 判定ロジック詳細 (全 17 項目)

```text
skills/grant_eligibility_checker/scripts/check_eligibility.py
├── EligibilityChecker (メインオーケストレーター)
├── Stage1RuleEvaluator (確定ルール 5項目判定)
├── Stage2DocumentMatcher (必要書類 4項目 集合差分判定)
└── Stage3SemanticEvaluator (定性 8項目 pgvectorコサイン類似度 & ルール判定)
```

### ① Stage 1: 確定ルール判定 (5 項目)
DB カラム同士を直接比較。不合格項目がある場合、全体の総合適合ステータスは `FAIL` となる。

1. **organization_type**: `npo_profiles.organization_type IN grants.eligible_org_types`
2. **years_active**: `(現在の年 - establishment_year) >= min_years_active` (設立年未設定の場合は `0` 年扱い)
3. **target_area**: `grant_area == '全国'` OR `npo_loc == '全国'` OR 部分一致 (`grant_area in npo_loc` / `npo_loc in grant_area`)
4. **budget_ratio**: `grants.amount_max <= npo_profiles.annual_budget * 0.50` (助成上限が年予算の 50% 以内なら適正)
5. **grant_status**: `grants.status == 'OPEN'` かつ `grants.deadline >= 現在日`

### ② Stage 2: 書類自動突合判定 (4 項目)
`grants.required_documents` と `npo_profiles.prepared_documents` を比較。
- 差分集合 `missing_docs = required_documents - prepared_documents` を抽出。
- スコア: `(一致数 / 必要数) * 100`（`required_documents` が明示的空配列の場合は 100点）。

### ③ Stage 3: pgvector セマンティック評価 & 定性判定 (8 項目)

| # | 項目キー | 判定ロジック | スコア算出・根拠 |
|---|---------|-------------|----------------|
| 10 | `activity_category` | NPO `chunk_type='ACTIVITY_TAGS'` のベクトルと `knowledge_chunks` の **pgvector コサイン類似度** | 類似度 (0.0~1.0) × 100。最類似チャンクテキストを `quote` に付与 |
| 11 | `target_audience` | NPO `chunk_type='TARGET_AUDIENCE'` のベクトルと `knowledge_chunks` の **pgvector コサイン類似度** | 類似度 (0.0~1.0) × 100。最類似チャンクテキストを `quote` に付与 |
| 12 | `purpose_match` | NPO `chunk_type='DESCRIPTION'` のベクトルと `knowledge_chunks` の **pgvector コサイン類似度** | 類似度 (0.0~1.0) × 100。最類似チャンクテキストを `quote` に付与 |
| 13 | `partnership_req` | `detail_text` 内のキーワード検出 (「連携」「協働」「パートナー」「地域住民」等) | キーワード検出時: 90点 / 未検出時: 75点 |
| 14 | `uniqueness_req` | `detail_text` 内のキーワード検出 (「新規」「先進」「モデル」「革新」等) | キーワード検出時: 90点 / 未検出時: 80点 |
| 15 | `cost_burden` | `grants.is_rate_10_10` フラグ参照 | `TRUE` (10/10 100%補助): 100点 / `FALSE`: 80点 |
| 16 | `advance_payment` | `grants.is_advance_payment` フラグ参照 | `TRUE` (概算払い可): 100点 / `FALSE`: 75点 |
| 17 | `compliance` | `detail_text` 内の暴力団・反社排除条項テキスト確認 | 規定あり: 100点 |

#### 🛡️ Substring Match Guard (完全確定引用)
- 項目 10〜12 で取得された `knowledge_chunks.content` の最類似フレーズを `evidence_quote` として採用。
- DB 内のテキストそのものを抽出するため、ハルシネーション率は **0%** が完全保証される。

---

## 5. 判定結果データ構造 (Output Schema)

```json
{
  "grant_id": 123,
  "grant_title": "令和8年度 地域コミュニティ活性化助成金",
  "npo_profile_id": "uuid-1234-5678",
  "npo_name": "特定非営利活動法人 まちづくりサポート",
  "match_score": 88,
  "status": "PASS",
  "stage1_results": {
    "all_pass": true,
    "details": {
      "organization_type": {"pass": true, "reason": "団体型 'NPO_CORPORATION' は対象枠 ['NPO_CORPORATION', 'GENERAL_INC'] に含まれます"},
      "years_active": {"pass": true, "reason": "活動実績 5年 (必要年数: 1年)"},
      "target_area": {"pass": true, "reason": "公募エリア '全国' vs 団体拠点 '東京都'"},
      "budget_ratio": {"pass": true, "reason": "助成上限 2,000,000円 / 前年予算 10,000,000円 (比率: 20.0% <= 50%上限)"},
      "grant_status": {"pass": true, "reason": "ステータス 'OPEN' / 締切 '2026-09-30'"}
    }
  },
  "stage2_results": {
    "score": 75,
    "required": ["ARTICLES", "BOARD_LIST", "FINANCIAL_REPORT", "REGISTRY_CERTIFICATE"],
    "prepared": ["ARTICLES", "BOARD_LIST", "FINANCIAL_REPORT"],
    "missing": ["REGISTRY_CERTIFICATE"]
  },
  "stage3_results": {
    "score": 86,
    "criteria_scores": {
      "activity_category": 92,
      "target_audience": 88,
      "purpose_match": 85,
      "partnership_req": 90,
      "uniqueness_req": 80,
      "cost_burden": 100,
      "advance_payment": 75,
      "compliance": 100
    },
    "evidence_quotes": [
      "地域のデジタル化を推進し、高齢者および過疎地域の住民を支援対象とする事業を助成します。"
    ]
  },
  "evaluated_at": "2026-07-31T22:45:00.000000"
}
```

---

## 6. DB 保存処理 (public.alerts)

判定完了後、Neon DB の `public.alerts` に PostgreSQL `ON CONFLICT` で保存・更新：

```sql
INSERT INTO public.alerts (npo_profile_id, grant_id, alert_type, title, message, match_score, is_read)
VALUES (%s, %s, 'ELIGIBILITY_MATCH', %s, %s, %s, false)
ON CONFLICT (npo_profile_id, grant_id, alert_type)
DO UPDATE SET
    title = EXCLUDED.title,
    message = EXCLUDED.message,
    match_score = EXCLUDED.match_score,
    is_read = false,
    created_at = NOW();
```
