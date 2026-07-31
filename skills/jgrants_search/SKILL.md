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

#### スクリプトの実行方法

```bash
# 基本検索 (キーワード指定)
uv run skills/jgrants_search/scripts/search_jgrants.py --keyword "地域"

# 「補助率 10/10 (全額補助)」のみに絞り込んで検索
uv run skills/jgrants_search/scripts/search_jgrants.py --rate-10-10

# 「全国」対象かつ「10/10 助成金」を検索
uv run skills/jgrants_search/scripts/search_jgrants.py --area "全国" --rate-10-10

# 助成上限額 100 万円以上の 10/10 助成金を検索
uv run skills/jgrants_search/scripts/search_jgrants.py --rate-10-10 --min-amount 1000000

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
| `--org-id` | `string` | 登録団体 ID (指定した団体の要件・活動・提出書類と自動照合) | `--org-id "org-uuid-1234"` |
| `--limit` | `int` | 出力件数の上限 (デフォルト: 10) | `--limit 10` |
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

### Phase 3.5: 団体プロファイル照合 ＆ 17項目適合チェック (`--org-id` 指定時)
1. **Stage 1 (確定要件判定)**: 法人格一致 (`organization_type`)・活動年数 (`establishment_year`)・活動エリア・予算規模を 0/1 チェック。
2. **Stage 2 (必要書類突合)**: 助成金の `required_documents` と団体の `prepared_documents` を集合比較し、不足書類を差分抽出。
3. **Stage 3 (LLM根拠抽出)**: 活動分野・目的の適合性を公募要領テキストの引用句 (`evidence_quote`) 付きで評価し、適合度スコア (0〜100%) を算出。

### Phase 4: DB 保存 (`public.grants`) & Office 連携
* **DB マッピング**: `source_grant_id`, `title`, `provider`, `amount_min`, `amount_max`, `deadline`, `details_url`, `target_area`, `is_rate_10_10`, `is_advance_payment`, `eligible_org_types`, `min_years_active`, `required_documents`, `detail_text`, `payload_json` へ書き込み。
* **Office 出力**: CSV / JSON 出力後、`officecli` を用いて `.xlsx` および `.docx` に変換。

---

## 関連ドキュメント・仕様書

* 📘 **データパイプライン詳細仕様**: [grant_pipeline_spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/grant_pipeline_spec.md) (差分同期, 添付OCR, RAGベクトル化, レート制御, アラート)
* 🗄️ **データベース全体設計**: [specifications.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/specifications.md#L195-L230) (`public.grants` テーブルスキーマ)


