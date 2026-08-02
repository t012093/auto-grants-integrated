---
name: past_award_analyzer
description: 対象助成金の過去採択事例データ (public.grant_past_awards) の自動収集 (crawl_past_awards.py) および5大視点分析 (analyze_past_awards.py) を行い、勝因パターン抽出と自社申請案への自動フィッティングを行う統合スキル。
---

# 過去採択事例 リサーチ & 勝因分析統合スキル (past_award_analyzer)

## 概要

助成金の過去の採択事例（採択団体名・事業名・金額・選定評コメント等）を実Webサイトから自動収集 (`crawl_past_awards.py`) して `public.grant_past_awards` に格納し、審査員が高く評価する「勝因パターン」を 5 大視点で構造的に分析 (`analyze_past_awards.py`) します。

分析結果を自社の申請案と照合し、差別化ポイントや改善アドバイスを自動生成します。

---

## 付属スクリプト

### 1. `scripts/crawl_past_awards.py` 収集・パース CLI
対象財団・行政のプロファイル (`profiles/*.json`) または指定 URL から過去採択結果 (PDF/WEB) を自動クロールし、数値・テキストを正規化して DB (`public.grant_past_awards`) へ Upsert 保存します。

```bash
# プロファイル指定で過去採択事例を自動収集・DB保存
uv run skills/past_award_analyzer/scripts/crawl_past_awards.py --grant-id 1 --profile pvt_nippon_foundation --save-db

# 指定 URL からの即時抽出 & JSON 出力
uv run skills/past_award_analyzer/scripts/crawl_past_awards.py --grant-id 1 --url "https://example.org/results.html" --json
```

### 2. `scripts/analyze_past_awards.py` 5大視点分析 CLI
DB (`public.grant_past_awards`) 内の過年度採択データを集計し、5大視点の勝因パターンおよび自社プロファイルとの勝因ギャップを分析します。

```bash
# 対象助成金の過去採択事例を分析
uv run skills/past_award_analyzer/scripts/analyze_past_awards.py --grant-id 1

# 自社プロファイルとの勝因ギャップ分析を含める
uv run skills/past_award_analyzer/scripts/analyze_past_awards.py --grant-id 1 --org-id "org-uuid-1234"

# JSON 形式で結果出力
uv run skills/past_award_analyzer/scripts/analyze_past_awards.py --grant-id 1 --json
```

---

## プロファイル構成 (`profiles/`)

対象サイトの DOM 構造や CSS セレクターを定義した JSON ファイル群。

- `pvt_nippon_foundation.json`: 公益財団法人 日本財団の過去採択結果定義
- `gov_cao_janpia.json`: JANPIA 休眠預金活用事業の過去採択結果定義

---

## パラメーター仕様一覧

### `analyze_past_awards.py`

| パラメーター | 型 | 説明 | 使用例 |
|---|---|---|---|
| `--grant-id` | `int` | 対象助成金の DB ID（デフォルト: 1） | `--grant-id 42` |
| `--org-id` | `string` | 自社団体 ID（勝因ギャップ分析を追加実行） | `--org-id "org-uuid-1234"` |
| `--auto-fetch` | `flag` | 自動探索・分析モード | `--auto-fetch` |
| `--register-json` | `string` | JSON データの直接登録（モード2） | `--register-json '[{...}]'` |
| `--json` | `flag` | JSON 形式で結果出力 | `--json` |

---

## 5 大分析視点 (SOP)

### Step 1: 採択事例データの自動収集・正規化
`crawl_past_awards.py` により `public.grant_past_awards` に対象助成金 / 財団の過去事例を集約。

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
