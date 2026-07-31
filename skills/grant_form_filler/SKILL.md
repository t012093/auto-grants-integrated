---
name: grant_form_filler
description: ハーネス検証済みの経費ポートフォリオ・17項目適合データ・過去採択勝因パターンを統合し、申請書の主要項目（事業目的・背景・実施計画・KPI・経費明細）を自動起草して officecli 経由で Word (.docx) / Excel (.xlsx) を生成するスキル。
---

# 申請書自動起草 & Office 出力スキル (grant_form_filler)

## 概要

要件適合チェック (`grant_eligibility_checker`) および経費最適化 (`grant_expense_validator`) を通過したデータと、過去採択事例の勝因パターン (`past_award_analyzer`) を統合し、申請書の主要セクションを自動起草します。生成した原稿は `officecli` を経由して Word (`.docx`) および Excel (`.xlsx`) 形式にエクスポートします。

---

## 付属スクリプト

### `scripts/generate_proposal_docx.py` 📋 実装予定
申請原稿の自動生成と Office ドキュメントへの出力を実行する CLI スクリプト。

#### 実行方法

```bash
# 申請書ドラフトを Word 形式で生成
uv run skills/grant_form_filler/scripts/generate_proposal_docx.py \
  --org-id "org-uuid-1234" --grant-id "g-456"

# 経費明細を Excel 形式で同時生成
uv run skills/grant_form_filler/scripts/generate_proposal_docx.py \
  --org-id "org-uuid-1234" --grant-id "g-456" --with-budget-xlsx

# Markdown 中間ファイルのみ出力（Office 変換なし）
uv run skills/grant_form_filler/scripts/generate_proposal_docx.py \
  --org-id "org-uuid-1234" --grant-id "g-456" --markdown-only
```

---

## パラメーター仕様一覧

| パラメーター | 型 | 説明 | 使用例 |
|---|---|---|---|
| `--org-id` | `string` | 登録団体 UUID | `--org-id "org-uuid-1234"` |
| `--grant-id` | `string` | 対象助成金 ID | `--grant-id "g-456"` |
| `--with-budget-xlsx` | `flag` | 経費明細 Excel を同時生成 | `--with-budget-xlsx` |
| `--markdown-only` | `flag` | Markdown 中間ファイルのみ出力 | `--markdown-only` |
| `--output-dir` | `string` | 出力先ディレクトリ（デフォルト: `.output/`） | `--output-dir "./proposals"` |

---

## 自動起草セクション (SOP)

### Step 1: 入力データの統合
以下のデータソースを統合して申請原稿の素材を構成します。

| データソース | 使用内容 |
|---|---|
| `npo_profiles` | 団体名、活動概要、実績、活動分野タグ |
| `grants` | 助成金名、目的、対象要件、公募要領テキスト |
| `grant_expense_rules` + `npo_expense_preferences` | 最適化済み経費ポートフォリオ |
| `grant_past_awards` (分析済み) | 勝因パターン・KPI 相場・タイトル提案 |

### Step 2: 申請書セクション自動起草
以下の主要セクションを LLM で起草します。各セクションには公募要領からの引用根拠を付与。

1. **事業の背景・社会的課題**: 地域統計データや当事者ニーズを引用
2. **事業目的**: 助成金の趣旨と団体ミッションの合致点を明示
3. **実施計画・スケジュール**: 月別の活動計画表
4. **実施体制**: 団体メンバー・連携先の役割分担
5. **期待される成果 (KPI)**: 過去採択事例の相場を参考にした定量目標
6. **経費明細 (自動計算済み)**: Solver 確定済みの経費ポートフォリオをそのまま転記

### Step 3: ハーネス検証 & Office 出力
* **Harness Guard**: 経費合計の算術一致・必須セクションの存在を最終検証。
* **Word 出力**: `officecli add <file>.docx /body --type markdown --prop src=draft.md`
* **Excel 出力**: `officecli import <file>.xlsx "/sheet[1]" budget.csv`

---

## 関連ドキュメント

* 📘 [grant_pipeline_spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/grant_pipeline_spec.md) (パイプライン統合仕様 §10)
* 🛠️ [grant_expense_validator SKILL.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/skills/grant_expense_validator/SKILL.md) (経費最適化の上流)
* 🔍 [past_award_analyzer SKILL.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/skills/past_award_analyzer/SKILL.md) (勝因パターンの上流)
