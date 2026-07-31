---
name: grant_expense_validator
description: 助成金の公募要領・細則・Q&Aから確定的パース（ハルシネーション0%）で抽出した経費ルール (public.grant_expense_rules) と、団体の経費希望優先度 (public.npo_expense_preferences) を機械的制約解決アルゴリズムで照合し、対象外経費を100%排除した最適経費ポートフォリオを動的に自動生成するスキル。
---

# 経費ルール検証 & 動的配分スキル (grant_expense_validator)

## 概要

公募要領・細則・Q&A から確定的パース（ハルシネーション 0%）で抽出済みの経費ルール (`public.grant_expense_rules`) と、団体が登録した経費使用の希望優先度 (`public.npo_expense_preferences`) を **LLM を介さない機械的制約解決アルゴリズム (Deterministic Constraint Solver)** で照合し、対象外経費を 100% 排除した最適な経費ポートフォリオを動的に自動生成します。

---

## 付属スクリプト

### `scripts/validate_expenses.py` 📋 実装予定
経費ルールと団体希望優先度の照合・最適配分を実行する CLI スクリプト。

#### 実行方法

```bash
# 団体の希望優先度と助成金ルールから最適経費ポートフォリオを生成
uv run skills/grant_expense_validator/scripts/validate_expenses.py \
  --org-id "org-uuid-1234" --grant-id "g-456"

# 手動入力の経費案をルールチェック（レガシー: 事後チェックモード）
uv run skills/grant_expense_validator/scripts/validate_expenses.py \
  --grant-id "g-456" --input-budget "budget.json"

# JSON 形式で結果出力
uv run skills/grant_expense_validator/scripts/validate_expenses.py \
  --org-id "org-uuid-1234" --grant-id "g-456" --json
```

---

## パラメーター仕様一覧

| パラメーター | 型 | 説明 | 使用例 |
|---|---|---|---|
| `--org-id` | `string` | 登録団体 UUID（希望優先度から動的配分を実行） | `--org-id "org-uuid-1234"` |
| `--grant-id` | `string` | 対象助成金 ID | `--grant-id "g-456"` |
| `--input-budget` | `string` | 手動入力の経費案 JSON ファイルパス（事後チェック用） | `--input-budget "budget.json"` |
| `--json` | `flag` | JSON 形式で結果出力 | `--json` |

---

## 動的配分アルゴリズム (SOP)

### Step 1: データ読み込み
* `public.grant_expense_rules` から対象助成金の全経費区分ルールを取得。
* `public.npo_expense_preferences` から団体の希望優先度リストを取得。

### Step 2: 優先度順 機械的配分ループ
1. 優先度の高い経費区分 (priority=1) から順に走査。
2. `allowed = FALSE` の区分 → **スキップ** し、理由 (`notes`) と代替経費区分を提案。
3. `allowed = TRUE` の区分 → `min(desired_amount, max_limit, remaining_total * max_ratio)` で配分額を確定。
4. 残枠がある場合は次の優先度の区分へ繰り越し。

### Step 3: ハーネス安全検算 (Harness Guard)
* 各経費区分の配分合計が助成上限額 (`amount_max`) を超過していないか算術検証。
* 補助率 × 経費 = 申請額 の計算一致を確認。
* 全バリデーション合格後のみ結果を出力。

---

## 出力フォーマット

```text
【最適経費ポートフォリオ: ○○助成金 (上限200万円 / 10/10)】

 ✅ 優先度1: 人件費       → 100万円 確定 (上限100万円)
 ❌ 優先度2: API使用料    → 排除 (理由: 月額サブスク経費は対象外)
 ✅ 優先度3: システム開発  → 70万円 確定 (上限150万円)
 残枠: 30万円 → 💡 「広報宣伝費」への増額を推奨

 合計: 170万円 / 200万円 (補助対象率: 100%)
```

---

## 関連ドキュメント

* 📘 [grant_pipeline_spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/grant_pipeline_spec.md) (パイプライン統合仕様 §7)
* 🗄️ [specifications.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/specifications.md) (`public.grant_expense_rules`, `public.npo_expense_preferences` DDL)
