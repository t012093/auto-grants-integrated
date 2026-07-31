---
name: grant_eligibility_checker
description: 登録団体プロファイル (--org-id) と助成金の公募要件を全17項目（法人格・実績年数・対象地域・事業予算・準備書類等）にわたって自動照合し、3段階ハイブリッド判定（確定ルール / 書類突合 / LLM根拠引用）で要件適合性を評価するスキル。
---

# 助成金要件適合チェッカー (grant_eligibility_checker)

## 概要

登録団体のプロファイル情報 (`public.npo_profiles`) と助成金の公募要件 (`public.grants`) を全 17 項目にわたって突き合わせ、要件適合性 (Eligibility Clearance) および必要書類の準備状況を全自動で判定します。

---

## 付属スクリプト

### `scripts/check_eligibility.py` 📋 実装予定
団体 ID と助成金 ID を指定して 17 項目の適合チェックを実行する CLI スクリプト。

#### 実行方法

```bash
# 基本実行: 団体と助成金の適合チェック
uv run skills/grant_eligibility_checker/scripts/check_eligibility.py \
  --org-id "org-uuid-1234" --grant-id "g-456"

# JSON 形式で結果出力
uv run skills/grant_eligibility_checker/scripts/check_eligibility.py \
  --org-id "org-uuid-1234" --grant-id "g-456" --json
```

---

## パラメーター仕様一覧

| パラメーター | 型 | 説明 | 使用例 |
|---|---|---|---|
| `--org-id` | `string` | 登録団体の UUID | `--org-id "org-uuid-1234"` |
| `--grant-id` | `string` | 対象助成金の ID | `--grant-id "g-456"` |
| `--json` | `flag` | JSON 形式で結果出力 | `--json` |

---

## 3 段階ハイブリッド判定フロー (SOP)

### Stage 1: ルールベース確定判定 (Deterministic Matching)
ハルシネーションゼロで確定判定する条件。LLM を一切使用しません。

1. **法人格チェック**: `npo_profiles.organization_type IN grants.eligible_org_types`
2. **実績年数チェック**: `EXTRACT(YEAR FROM NOW()) - npo_profiles.establishment_year >= grants.min_years_active`
3. **対象地域チェック**: `npo_profiles.location IN grants.target_area` (「全国」は常にマッチ)
4. **予算規模チェック**: `grants.amount_max` と `npo_profiles.annual_budget` の適正比率判定

### Stage 2: 書類自動突合判定 (Document Readiness Matching)
`grants.required_documents` と `npo_profiles.prepared_documents` を集合比較し、差分（未準備書類リスト）を抽出。

### Stage 3: LLM セマンティック適合 + 根拠引用 (Semantic Quote Extraction)
活動分野・目的の定性的な適合性のみ LLM で評価。判定時に公募要領テキストの **原文引用句 (`evidence_quote`)** を必須付与。

---

## 出力フォーマット

* **適合スコア**: 0 〜 100%
* **17 項目個別判定**: 各項目の合格/不合格 + 理由
* **未準備書類リスト**: 申請に必要だが団体が未取得の書類一覧

---

## 関連ドキュメント

* 📘 [grant_pipeline_spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/grant_pipeline_spec.md) (パイプライン統合仕様)
* 🗄️ [specifications.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/specifications.md) (DB スキーマ)
