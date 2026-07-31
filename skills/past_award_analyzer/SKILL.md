---
name: past_award_analyzer
description: 対象助成金の過去採択事例データ (public.grant_past_awards) を収集・分析し、5大視点（課題切り口・連携体制・金額相場・KPI指標・選定評）から勝因パターンを抽出して自社申請案に自動フィッティングするスキル。
---

# 過去採択事例分析スキル (past_award_analyzer)

## 概要

助成金の過去の採択事例（採択団体名・事業名・金額・選定評コメント等）を `public.grant_past_awards` から取得し、審査員が高く評価する「勝因パターン」を 5 大視点で構造的に分析します。分析結果を自社の申請案と照合し、差別化ポイントや改善アドバイスを自動生成します。

---

## 付属スクリプト

### `scripts/analyze_past_awards.py`
対象助成金 ID を指定して過去採択事例の勝因パターンを分析する CLI スクリプト。

#### 実行方法

```bash
# 対象助成金の過去採択事例を分析
uv run skills/past_award_analyzer/scripts/analyze_past_awards.py --grant-id "g-456"

# 自社プロファイルとの勝因ギャップ分析を含める
uv run skills/past_award_analyzer/scripts/analyze_past_awards.py \
  --grant-id "g-456" --org-id "org-uuid-1234"

# JSON 形式で結果出力
uv run skills/past_award_analyzer/scripts/analyze_past_awards.py \
  --grant-id "g-456" --json
```

---

## パラメーター仕様一覧

| パラメーター | 型 | 説明 | 使用例 |
|---|---|---|---|
| `--grant-id` | `string` | 分析対象の助成金 ID | `--grant-id "g-456"` |
| `--org-id` | `string` | 自社団体 ID（勝因ギャップ分析を追加実行） | `--org-id "org-uuid-1234"` |
| `--years` | `int` | 分析対象年数（デフォルト: 3） | `--years 5` |
| `--json` | `flag` | JSON 形式で結果出力 | `--json` |

---

## 5 大分析視点 (SOP)

### Step 1: 採択事例データ収集
`public.grant_past_awards` から対象助成金の `funder_name` に紐づく過去 N 年分の採択一覧を取得。

### Step 2: 5 大視点クラスタリング

| # | 視点 | 抽出・分析内容 |
|:---:|---|---|
| 1 | **課題設定の切り口** (Problem Framing) | 採択事業がどんな社会課題を提示したか。データ・統計引用の有無。 |
| 2 | **解決アプローチ・体制** (Solution Model) | 単独 vs 連携体制（行政・企業・他NPO）。後援・協力の有無。 |
| 3 | **金額・予算の相場感** (Budget Range) | 平均採択額・満額率・最頻申請額帯。 |
| 4 | **定量成果・KPI 指標** (Impact Metrics) | 提示された数値目標（受益者数、イベント回数等）。 |
| 5 | **審査評・選定理由** (Evaluator Feedback) | 選定評コメントのキーワード（「連携」「継続性」「波及効果」等）。 |

### Step 3: 自社申請案への勝因フィッティング (`--org-id` 指定時)
自社プロファイル (`npo_profiles.description`, `activity_tags`) と勝因パターンのギャップを算出し、タイトル修正提案・体制強化提案・KPI 設計アドバイスを自動生成。

---

## 関連ドキュメント

* 📘 [grant_pipeline_spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/grant_pipeline_spec.md) (パイプライン統合仕様 §8)
* 🗄️ [specifications.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/specifications.md) (`public.grant_past_awards` DDL)
