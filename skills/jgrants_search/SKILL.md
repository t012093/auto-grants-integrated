---
name: jgrants_search
description: デジタル庁 jGrants 公式 API に接続し、全国・富山県などの地域条件や「補助率 10/10 (全額補助・自己負担0%)」「概算払い/前払い」等の条件で助成金・公募情報を検索・自動抽出するスキル。
---

# jGrants 助成金・公募検索スキル (jgrants_search)

## 概要

デジタル庁が提供する **jGrants 公式 REST API** (`https://api.jgrants-portal.go.jp/exp/v1/public/subsidies`) に直接接続し、現在募集中の最新助成金・補助金データを条件検索・抽出します。

本スキルには、以下の高度な抽出・フィルタリング機能が組み込まれています：
1. **補助率 10/10 (定額・全額補助)** の自動判定・抽出
2. **前払い (概算払い・前金交付)** 記載の自動検出
3. **地域指定 (全国・富山県等)** によるエリアフィルタリング

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

# 「富山県」対象かつ「10/10 助成金」を検索
uv run skills/jgrants_search/scripts/search_jgrants.py --area "富山" --rate-10-10

# 「前払い / 概算払い」記載のある助成金を検索
uv run skills/jgrants_search/scripts/search_jgrants.py --advance-payment

# 条件の組み合わせ (全国対象 + 10/10 + 最大5件)
uv run skills/jgrants_search/scripts/search_jgrants.py --area "全国" --rate-10-10 --limit 5
```

---

## パラメーター仕様一覧

| パラメーター | 型 | 説明 | 使用例 |
|---|---|---|---|
| `--keyword` | `string` | 検索キーワード（2文字以上推奨） | `--keyword "NPO"` |
| `--area` | `string` | 対象地域（都道府県名または「全国」） | `--area "富山県"` |
| `--rate-10-10` | `flag` | 補助率 10/10 (定額・全額補助) のみに絞り込む | `--rate-10-10` |
| `--advance-payment` | `flag` | 概算払い・前払い記載のあるものに絞り込む | `--advance-payment` |
| `--limit` | `int` | 出力件数の上限 (デフォルト: 10) | `--limit 5` |

---

## ディレクトリ構造

```
skills/jgrants_search/
├── SKILL.md                          # 本仕様・マニュアル書
└── scripts/
    └── search_jgrants.py             # jGrants リアルタイム検索 CLI スクリプト
```

---

## 抽出判定ロジック

* **補助率 10/10 判定**:
  詳細 API の `subsidy_rate` または本文テキストに対し、正規表現 `10/10`, `10分の10`, `定額`, `全額補助`, `100%` を確定マッピング。
* **前払い / 概算払い 判定**:
  詳細本文に対し、正規表現 `概算払`, `前払`, `前金`, `事前交付` をマッピング。
