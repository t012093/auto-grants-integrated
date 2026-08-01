---
name: grant_form_filler
description: ハーネス検証済みの経費ポートフォリオ・17項目適合データ・過去採択勝因パターンを統合し、申請書の主要項目（事業目的・背景・実施計画・KPI・経費明細）を自動起草して officecli 経由で Word (.docx) / Excel (.xlsx) を生成するスキル。
---

# 申請書自動起草 & Office 出力スキル (grant_form_filler)

## 概要

要件適合チェック (`grant_eligibility_checker`) および経費最適化 (`grant_expense_validator`) を通過したデータと、過去採択事例の勝因パターン (`past_award_analyzer`) を統合し、申請書の主要 6 大セクションを自動起草します。生成した原稿は `officecli` を経由して提出用 Word (`.docx`) および Excel (`.xlsx`) 形式に自動エクスポートします。

---

## 🏗️ 設計思想: ノンインタラクティブ & 安全フォールバック原則

自動化パイプライン (CLIバッチ, Webシステム, Cron) での連続実行を止めないため、以下の設計原則を適用します。

1. **デフォルト自動補完 (ノンインタラクティブ完走)**:
   - 過去採択事例データが 0 件の場合 ➡ 同ジャンル助成金の「標準成果目標 (参加者数・満足度等)」を自動生成して適用。
   - NPO希望経費が未設定の場合 ➡ デフォルト標準配分 (人件費50%, システム費30%, 広報費20%) で自動補完。
2. **自動補完注記の明示**:
   - 自動補完された項目は、出力原稿 (Markdown/Word) 内に `💡 [自動補完注記: 過去採択事例未登録のため標準KPIを補完]` のように明記し、人間による追推稿・確認を容易にする。
3. **厳格モード (`--strict`) のサポート**:
   - オプション `--strict` を指定した場合のみ、データ不足時に自動補完を行わずエラー表示で処理を中断する。

---

## 付属スクリプト

### `scripts/generate_proposal_docx.py` 📋 実装予定
申請原稿の自動生成と Office ドキュメントへの出力を実行する CLI スクリプト。

#### 実行方法

```bash
# 基本実行 (フリーフォーマット Word ファイルを生成)
uv run skills/grant_form_filler/scripts/generate_proposal_docx.py \
  --org-id "org-uuid-1234" --grant-id "g-456"

# 経費明細 Excel (.xlsx) を同時生成
uv run skills/grant_form_filler/scripts/generate_proposal_docx.py \
  --org-id "org-uuid-1234" --grant-id "g-456" --with-budget-xlsx

# 指定様式テンプレート (.docx) を使用して {{key}} をプレースホルダー置換
uv run skills/grant_form_filler/scripts/generate_proposal_docx.py \
  --org-id "org-uuid-1234" --grant-id "g-456" --template-docx "./templates/form.docx"

# データ不足時に自動補完を行わずエラー終了する厳格モード
uv run skills/grant_form_filler/scripts/generate_proposal_docx.py \
  --org-id "org-uuid-1234" --grant-id "g-456" --strict

# Markdown 中間ファイルのみ出力 (Office 変換なし)
uv run skills/grant_form_filler/scripts/generate_proposal_docx.py \
  --org-id "org-uuid-1234" --grant-id "g-456" --markdown-only
```

---

## パラメーター仕様一覧

| パラメーター | 型 | 説明 | 使用例 |
|---|---|---|---|
| `--org-id` | `string` | 登録団体 UUID (必須) | `--org-id "org-uuid-1234"` |
| `--grant-id` | `string` | 対象助成金 ID (必須) | `--grant-id "g-456"` |
| `--with-budget-xlsx` | `flag` | 経費明細 Excel (.xlsx) を同時生成 | `--with-budget-xlsx` |
| `--template-docx` | `string` | 指定様式 Word テンプレートファイルパス（未指定時はパターンAのフリーフォーマット生成） | `--template-docx "./template.docx"` |
| `--strict` | `flag` | データ未登録時に自動補完を行わずエラー中断する厳格モード | `--strict` |
| `--markdown-only` | `flag` | Markdown 中間ファイルのみ出力 | `--markdown-only` |
| `--output-dir` | `string` | 出力先ディレクトリ（デフォルト: `.output/`） | `--output-dir "./proposals"` |

---

## 自動起草プロシージャ (SOP)

### Step 1: 入力データの統合 & 自動補完
以下のデータソースを統合して申請原稿の素材を構成します（不足データはデフォルト補完）。

| データソース | 使用内容 | データ未登録時の自動補完動作 |
|---|---|---|
| `npo_profiles` | 団体名、活動概要、実績、活動分野 | 団体基本情報から概略文を自動補完 |
| `grants` | 助成金名、目的、対象要件、公募要領テキスト | タイトル・公募内容よりテキスト抽出 |
| `grant_expense_rules` + `npo_expense_preferences` | 最適化済み経費ポートフォリオ | デフォルト配分 (人件費50%, システム費30%, 広報費20%) で補完 |
| `grant_past_awards` (分析済み) | 勝因パターン・KPI 相場・タイトル提案 | 同ジャンル標準成果目標 (参加者数・満足度等) で補完 |

### Step 2: 申請書 6 大セクション自動起草
公募要領からの原文引用を根拠として以下の標準スタイルで付与し、主要セクションを自動起草します。
> **【公募要領 引用】** 「本助成金は地域コミュニティのデジタル化を推進し、持続可能な活動基盤を構築することを目的とします。」

1. **事業の背景・社会的課題**: 地域統計データや当事者ニーズ、公募要領の原文引用
2. **事業目的**: 助成金の趣旨と団体ミッションの合致点を明示
3. **実施計画・月別スケジュール**: 助成期間に応じた活動計画表。※要綱に明確な事業期間の記載がない場合は、標準12ヶ月間 (4月〜翌3月) で仮起草し、`💡 [要確認: 公募要領に事業期間の明確な記載がないため、標準12ヶ月間 (4月〜翌3月) として仮生成しています。正式な事業対象期間を確認してください]` の【要確認注記】を自動付与。
4. **実施体制**: 団体メンバー・連携パートナーの役割分担表
5. **期待される成果 (KPI)**: 過去採択相場（未登録時は標準目標）に合わせた定量成果
6. **経費明細 (自動計算済み)**: Solver 確定済みの経費ポートフォリオをハルシネーション0%でそのまま転記

### Step 3: 書類様式事前分析 (Format Analysis & Profiling)
提出先ごとに異なる公式様式ファイルの構造を、**書き込みの前に完全解析**して動的マッピングを生成します。

#### 3-1. 構造スキャン & 様式タイプ自動分類
```bash
# テンプレート内の全段落・フォーム枠・表を JSON で抽出
officecli query template.docx "//p" --json
officecli query template.docx "sdt" --json
officecli query template.docx "table" --json
```

スキャン結果から以下の 3 タイプに自動分類します。

| 様式タイプ | 判定条件 | 書き込み方式 |
|---|---|---|
| **タイプ A: マーカー型** | テキスト内に `{{事業目的}}` 等のマーカーが存在 | `officecli merge` (JSON データ置換) |
| **タイプ B: フォーム型** | Word フォーム枠 `sdt` が存在 | `officecli batch` で `sdt` パスへ直接 `set` |
| **タイプ C: 表構造型** | 表 (`table`) が主要構造。マーカーやフォーム枠なし | `officecli batch` でセルパス (`/body/tbl[N]/tr[M]/tc[K]`) へ直接 `set` |

#### 3-2. 動的ノードパス辞書の生成
タイプ B / C の場合、`officecli query` の JSON 結果からマーカー文字列やフォームタグの正確なノードパスを動的に辞書化します。

**安全ルール (堅牢化 3 原則)**:
1. **正規表現クレンジング**: `re.search(r"\{\{\s*(.*?)\s*\}\}", text)` でスペースや注釈付き表記ゆれを正規化。
2. **操作ソート**: 既存セルの `set` (更新) をすべて完了してから、テーブル末尾への `add` (行追加) を行う。パスインデックスのズレを防止。
3. **実行後再検証**: 流し込み直後に再スキャンし、`{{` 残存 = 0 件を強制確認。

---

### Step 4: アトミックバッチ流し込み (Batch Execution)
メモリ常駐プロセスを使用し、全データを 1 パスでアトミックに適用します。

#### 4-1. タイプ A (マーカー型) の場合: `officecli merge`
```bash
officecli merge template.docx output.docx \
  --data '{"事業背景":"...", "事業目的":"...", "経費合計":"3,000,000円"}' \
  --force
```

#### 4-2. タイプ B / C (フォーム型・表構造型) の場合: `officecli open` ➡ `batch` ➡ `close`
```bash
# 1. メモリ常駐ロード
officecli open template.docx

# 2. JSON Batch でアトミック流し込み (--stop-on-error で失敗時全ロールバック)
officecli batch template.docx --commands '[
  {"command":"set", "path":"/body/tbl[1]/tr[2]/tc[2]/p[1]", "props":{"text":"事業背景テキスト...", "font":"Yu Gothic", "size":"10.5pt"}},
  {"command":"set", "path":"/body/tbl[1]/tr[3]/tc[2]/p[1]", "props":{"text":"事業目的テキスト..."}},
  {"command":"set", "selector":"sdt[tag=経費合計]", "props":{"text":"3,000,000円"}},
  {"command":"add", "parent":"/body/tbl[2]", "type":"table-row", "props":{"values":["人件費","1,500,000円","承認"]}}
]' --stop-on-error

# 3. ディスクへ最終保存 & メモリ解放
officecli close template.docx
```

#### 4-3. テンプレートなし (フォールバック): Markdown 新規構築
```bash
officecli create output.docx
officecli add output.docx /body --type markdown --prop src=proposal.md
```

---

### Step 5: 多段検証ガード (Multi-Layer Verification)
ファイル保存前に 4 層の検証を実行し、**抜け・漏れ・ファイル破損を 100% 防止**します。

| 検証レイヤー | 検証内容 | 使用コマンド / ロジック |
|---|---|---|
| **Layer 1: 算術検証** | 経費合計が助成上限額を超過していないか | Python 内部ロジック |
| **Layer 2: 構造検証** | 必須 6 大セクションが漏れなく存在するか | Python 内部ロジック |
| **Layer 3: 未置換タグ残存ゼロ** | `{{key}}` がヘッダー・フッター・表セル含め全テキストに残存していないか | `officecli view <file> text` の全文出力を正規表現チェック |
| **Layer 4: OpenXML スキーマ適合** | 生成ファイルが Microsoft Office 規格に適合し壊れていないか | `officecli validate <file>` |

検証エラーが 1 つでもあれば、ファイル出力をストップし `HarnessValidationError` を発生させます。

---

### Step 6: Render-Look-Fix (視覚レイアウト自動補正)
生成した最終ドキュメントを HTML / 画像に変換して「見た目の崩れ」を視覚的に検証・修正するセルフヒーリングループです。

```bash
# HTML レンダリングで視覚確認
officecli view output.docx html -o preview.html

# スクリーンショットで画像キャプチャ (枠あふれ・重なり検知用)
officecli view output.docx screenshot -o preview.png
```

* 枠あふれ・レイアウト崩れが検出された場合 → セル幅・フォントサイズ・`fitText` を自動修正して再バッチ適用。
* 修正ループは**最大 2 回**まで。3 回目でも解決しない場合はユーザーに報告して手動校正を依頼。

---

### Step 7: Excel 経費明細出力 (`--with-budget-xlsx`)
公式 Excel 様式が存在する場合、数式セルを保護しつつデータセルのみを更新します。

```bash
# 1. 公式 Excel テンプレートの分析
officecli query budget_template.xlsx "//table-cell" --json

# 2. 数値入力セルのみを batch で更新 (計算式セルはスキップ)
officecli batch budget_template.xlsx --commands '[
  {"command":"set", "path":"/sheet[1]/B3", "props":{"text":"1500000"}},
  {"command":"set", "path":"/sheet[1]/B4", "props":{"text":"900000"}}
]' --stop-on-error

# 3. officecli 内蔵の 350+ 関数で合計行を自動再計算
```

公式 Excel がない場合は 5 カラム構成（経費区分, 項目名, 希望額, 助成対象決定額, 充当理由）で新規シートを作成:
```bash
officecli create budget.xlsx
officecli import budget.xlsx "/sheet[1]" budget.csv
```

---

## 関連ドキュメント

* 📘 [grant_pipeline_spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/grant_pipeline_spec.md) (パイプライン統合仕様 §10)
* 🛠️ [grant_expense_validator SKILL.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/skills/grant_expense_validator/SKILL.md) (経費最適化の上流)
* 🔍 [past_award_analyzer SKILL.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/skills/past_award_analyzer/SKILL.md) (勝因パターンの上流)

