# スキル仕様書: 17項目要件適合チェッカー (check_eligibility.py)

## 1. 概要 & 目的
登録団体のプロファイル情報 (`public.npo_profiles`) と助成金の公募要件 (`public.grants`) を受け取り、全 17 項目にわたる 3 段階ハイブリッド判定（確定ルール 5 項目 / 書類突合 4 項目 / LLM 定性引用 8 項目）で適合スコア (0-100%) と未準備書類差分を自動算出・`public.alerts` に保存する CLI スクリプト。

---

## 2. CLI インターフェース仕様

```bash
uv run skills/grant_eligibility_checker/scripts/check_eligibility.py \
  --org-id "<npo_profile_uuid>" \
  --grant-id "<grant_db_id_or_source_id>" \
  [--json]
```

### 引数
- `--org-id` (`str`, 必須): 判定対象の NPO 団体 UUID
- `--grant-id` (`str`, 必須): 判定対象の助成金 DB ID (`grants.id` または `source_grant_id`)
- `--json` (`flag`, 任意): 結果を JSON フォーマットで標準出力

---

## 3. クラス設計 & アーキテクチャ

```text
skills/grant_eligibility_checker/scripts/check_eligibility.py
├── EligibilityChecker (メインオーケストレーター)
├── Stage1RuleEvaluator (確定ルール 5項目判定 / LLM不使用誤り0%)
├── Stage2DocumentMatcher (必要書類 4項目 集合差分判定)
├── Stage3SemanticEvaluator (定性 8項目 セマンティック評価 & 原文引用 Guard)
└── DatabaseClient (Neon PostgreSQL / psycopg 接続・Alerts保存)
```

### モジュール詳細仕様

#### ① Stage1RuleEvaluator
1. **organization_type**: `npo_profiles.organization_type IN grants.eligible_org_types`
2. **years_active**: `(現在の年 - establishment_year) >= min_years_active`
3. **target_area**: `target_area == '全国'` または `npo_profiles.location` がマッチ
4. **budget_ratio**: `grants.amount_max <= npo_profiles.annual_budget * 0.50` (予算の50%以内が適正)
5. **grant_status**: `grants.status == 'OPEN'` かつ `grants.deadline >= 現在日`

#### ② Stage2DocumentMatcher
`grants.required_documents` と `npo_profiles.prepared_documents` を比較。
- 差分集合 `missing_docs = required_documents - prepared_documents` を抽出。

#### ③ Stage3SemanticEvaluator
LLM により 8 項目の適合度 (0-100%) と公募本文 (`grants.detail_text`) からの **原文引用句 (`evidence_quote`)** を抽出。
- **Substring Match Guard**: 返却された `evidence_quote` が `grants.detail_text` にそのまま存在するかコード検証し、存在しない場合はハルシネーションと見なして無効化。

---

## 4. 判定結果データ構造 (Output Schema)

```json
{
  "grant_id": 123,
  "npo_profile_id": "uuid-1234",
  "match_score": 92,
  "status": "PASS",
  "stage1_results": {
    "organization_type": {"pass": true, "reason": "NPO法人 (対象内)"},
    "years_active": {"pass": true, "reason": "活動年数3年 (必要:1年)"},
    "target_area": {"pass": true, "reason": "全国対象"},
    "budget_ratio": {"pass": true, "reason": "助成希望額200万/前年予算1000万 (20%)"},
    "grant_status": {"pass": true, "reason": "受付中 (締切: 2026-09-30)"}
  },
  "stage2_results": {
    "prepared": ["ARTICLES", "FINANCIAL_REPORT", "BOARD_LIST"],
    "missing": ["REGISTRY_CERTIFICATE"]
  },
  "stage3_results": {
    "activity_category": {"score": 95, "quote": "地域コミュニティのデジタル化を推進するNPO等を対象とする"},
    "target_audience": {"score": 90, "quote": "高齢者および過疎地域の住民を支援対象とする"}
  }
}
```

---

## 5. DB 保存処理 (public.alerts)

判定完了後、Neon DB の `public.alerts` に自動インサート：
- `npo_profile_id`: 対象団体 ID
- `grant_id`: 対象助成金 ID
- `alert_type`: `'ELIGIBILITY_MATCH'`
- `title`: `「【適合率 92%】{助成金タイトル}」`
- `message`: Stage 1-3 の判定要約と未準備書類リスト
- `match_score`: 適合度スコア (例: 92)
