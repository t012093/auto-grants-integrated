---
name: jgrants_search
description: デジタル庁 jGrants 公式 API に接続し、全国・富山県などの地域条件や「補助率 10/10 (全額補助・自己負担0%)」「概算払い/前払い」「助成額」等の条件で助成金・公募情報を検索・自動抽出および DB/Office ドキュメント出力するプロシージャルスキル。
---

# jGrants 助成金・公募検索スキル (jgrants_search)

## 概要

デジタル庁が提供する **jGrants 公式 REST API** (`https://api.jgrants-portal.go.jp/exp/v1/public/subsidies`) に直接接続し、現在募集中の最新助成金・補助金データを条件検索・抽出します。

本スキルには、以下のプロシージャルな抽出・フィルタリング機能が組み込まれています：
1. **横断キーワード自動取得**: jGrants API のキーワード必須仕様に対応し、主要4キーワード (`事業`, `補助金`, `助成金`, `支援`) で全件網羅取得
2. **補助率 10/10 (定額・全額補助)** の自動判定・抽出
3. **前払い (概算払い・前金交付)** 記載の自動検出
4. **地域指定 (全国・富山県等)** によるエリアフィルタリング
5. **助成金額範囲指定** (`--min-amount`, `--max-amount`)
6. **DB 保存マッピング (`public.grants`)** および Office CLI による Excel / Word 自動生成

---

## 付属スクリプト

### `scripts/search_jgrants.py`
デジタル庁公式 API と通信し、リアルタイムで助成金・公募情報を検索・抽出する汎用 CLI スクリプト。

### `scripts/extract_pdf.py`
`PyMuPDF` を用いて公募要領 PDF を超高速テキスト化し、審査基準・対象経費・事業期間等の要件を確定的かつハルシネーション 0% ガード付きで構造化抽出する CLI スクリプト。

#### スクリプトの実行方法

```bash
# 基本検索 (キーワード指定)
uv run skills/jgrants_search/scripts/search_jgrants.py --keyword "地域"

# 公募要領 PDF の自動パース & 要件構造化
uv run skills/jgrants_search/scripts/extract_pdf.py --grant-id 1

# ローカル指定 PDF からの自動パース & DB保存
uv run skills/jgrants_search/scripts/extract_pdf.py --pdf-path ./guideline.pdf --grant-id 1
```

# 「補助率 10/10 (全額補助)」のみに絞り込んで検索
uv run skills/jgrants_search/scripts/search_jgrants.py --rate-10-10

# 「全国」対象かつ「10/10 助成金」を検索
uv run skills/jgrants_search/scripts/search_jgrants.py --area "全国" --rate-10-10

# 助成上限額 100 万円以上の 10/10 助成金を検索
uv run skills/jgrants_search/scripts/search_jgrants.py --rate-10-10 --min-amount 1000000

# 助成上限額 100 万円以上の 10/10 助成金を検索し、自社 DB へ直接保存
uv run skills/jgrants_search/scripts/search_jgrants.py --rate-10-10 --min-amount 1000000 --save-db

# JSON 形式で出力
uv run skills/jgrants_search/scripts/search_jgrants.py --area "全国" --rate-10-10 --json
```

---

## パラメーター仕様一覧

| パラメーター | 型 | 説明 | 使用例 |
|---|---|---|---|
| `--keyword` | `string` | 検索キーワード（未指定時は主要キーワードで自動横断取得） | `--keyword "NPO"` |
| `--area` | `string` | 対象地域（都道府県名または「全国」） | `--area "全国"` |
| `--rate-10-10` | `flag` | 補助率 10/10 (定額・全額補助) のみに絞り込む | `--rate-10-10` |
| `--advance-payment` | `flag` | 概算払い・前払い記載のあるものに絞り込む | `--advance-payment` |
| `--min-amount` | `int` | 助成上限額の下限 (円) | `--min-amount 1000000` |
| `--max-amount` | `int` | 助成上限額の上限 (円) | `--max-amount 50000000` |
| `--limit` | `int` | 出力件数の上限 (デフォルト: 10) | `--limit 10` |
| `--save-db` | `flag` | 検索結果を自社 DB (`public.grants`) に自動 Upsert 保存 | `--save-db` |
| `--json` | `flag` | JSON 形式で標準出力 | `--json` |

---

## API仕様上の留意点と解決策 (Gotchas & Solutions)

1. **`keyword` パラメータ未指定時の 0 件動作**:
   * **問題**: jGrants API (`/subsidies`) は `keyword` パラメータが含まれない（または空文字）場合、**レスポンスが 0 件になる** 仕様です。
   * **解決策**: `--keyword` が指定されない場合は、公募カバー率 100% を達成するため主要 4 キーワード (`["事業", "補助金", "助成金", "支援"]`) で自動巡回リクエストを行い、`id` で重複排除します。
2. **単一キーワード検索での大量漏脱**:
   * **問題**: `"助成金"` 単一キーワードのみで検索した場合、全体 306 件中 54 件（約 17%）しか取得できず、82.3% の「〜補助金」「〜事業」案件が漏脱します。
   * **解決策**: 自動巡回により 306 件全件を対象として抽出処理を行います。

---

## プロシージャル・リサーチフロー (SOP)

### Phase 1: 横断データ収集 (Data Retrieval)
jGrants API の仕様上、`keyword` パラメータが未指定の場合は 0 件となるため、`--keyword` 未指定時は `["事業", "補助金", "助成金", "支援"]` で繰り返し取得し、`id` ベースで重複排除を実施。

### Phase 2: 詳細情報フェッチ (Detail Enrichment)
`/subsidies/id/{id}` API より `subsidy_rate`, `target_area_search`, `subsidy_max_limit`, `acceptance_end_datetime`, `detail` 本文を非同期バッチ取得。

### Phase 3: ルールベース判定 (Procedural Evaluation)
1. **エリア判定**: `target_area_search` の「全国」または指定都道府県マッチング
2. **10/10 判定**: 正規表現 `r"10/10"`, `r"10分の10"`, `r"１０／１０"`, `r"定額"`, `r"全額補助"`, `r"100%"` (IGNORECASE)
3. **前払い判定**: 正規表現 `r"概算払"`, `r"前払"`, `r"前金"`, `r"事前交付"`
4. **金額判定**: `subsidy_max_limit` のパース値比較

### Phase 3.5: 団体プロファイル照合 ＆ 17項目適合チェック
> **📋 本機能は [grant_eligibility_checker](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/skills/grant_eligibility_checker/SKILL.md) スキルに委譲しています。** 検索結果に対して団体適合チェックを行う場合は、検索後に `check_eligibility.py --org-id ... --grant-id ...` を実行してください。

### Phase 4: DB 保存 (`public.grants`) & 二重保存防止・安全制御 (`--save-db`)
* **二重保存防止 (Upsert)**: 複合ユニーク制約 `(source, source_grant_id)` に基づき `ON CONFLICT (source, source_grant_id) DO UPDATE` を実行。重複登録を 100% 排除。
* **破壊的上書きの防止**: 既存の `is_ocr_processed` フラグやパース済みの `grant_expense_rules`（経費ルール）を保持するため、更新対象を `title`, `provider`, `amount_max`, `deadline`, `details_url`, `target_area`, `is_rate_10_10`, `is_advance_payment`, `detail_text`, `status`, `updated_at` の動的カラムに限定。
* **事前バリデーション**: `source_grant_id`, `title` 欠損などの不完全データは DB 保存を自動スキップ。
* **Office 出力**: CSV / JSON 出力後、`officecli` を用いて `.xlsx` および `.docx` に変換。

---

## 関連ドキュメント・仕様書

* 📘 **データパイプライン詳細仕様**: [grant_pipeline_spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/grant_pipeline_spec.md) (差分同期, 添付OCR, RAGベクトル化, レート制御, アラート)
* 🗄️ **データベース全体設計**: [specifications.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/specifications.md#L195-L230) (`public.grants` テーブルスキーマ)


