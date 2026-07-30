# auto-grants-integrated 情報源リスト (Source Registry)

> **Version**: 1.0  
> **更新日**: 2026-07-31  
> **ステータス**: Draft  
> **関連仕様書**: [ingest_embedding_ui_spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/ingest_embedding_ui_spec.md)

---

## 1. 概要

本ドキュメントは、`auto-grants-integrated` プラットフォームが自動収集・構造化を行う **すべての情報源（助成金・補助金・行政施策・市民データ等）のマスターリスト** である。

各情報源に対して、以下の技術要素を定義・紐付けしている：
* **取得エンジン (Acquisition Engine)**: `httpx` (軽量API/RSS) vs `Crawl4AI × Camoufox` (統合 Web クローラー)
* **確定的抽出方式 (Deterministic Extraction)**: `JSON Direct` / `XML RSS` / `JsonCss` (CSSセレクター) / `markdownify`
* **更新頻度 & パイプライン**: ポーリング周期および重複判定ハッシュアルゴリズム

---

## 2. 情報源マスター一覧

### 2.1 カテゴリ A: 公的 API / 国家データ基盤 (Public APIs)

| Source ID | 情報源名称 | 管轄 / 運営 | エンドポイント / 参照 URL | 取得手法 | 確定的抽出方式 | 更新頻度 |
|---|---|---|---|---|---|---|
| `api_jgrants` | **jGrants API** | デジタル庁 | `https://api.jgrants-portal.go.jp/v1/subsidy` | `httpx` (API) | JSON Direct | 1日1回 |
| `api_jfc_navi` | **JFC 助成金ナビ** | 日本政策金融公庫 | `https://www.jfc.go.jp/n/finance/search/` | `httpx` (API) | JSON Direct | 1日1回 |
| `api_egov_data` | **e-Gov オープンデータ** | 総務省/デジタル庁 | `https://data.e-gov.go.jp/data/api/` | `httpx` (API) | JSON Direct | 週1回 |

---

### 2.2 カテゴリ B: 中央省庁・政府機関 (Central Ministries & Agencies)

| Source ID | 省庁・機関名称 | 主な対象領域 / 助成金種別 | 参照 URL / フィード | 取得手法 | 確定的抽出方式 | 更新頻度 |
|---|---|---|---|---|---|---|
| `gov_cfa` | **こども家庭庁** | こども育成・地域子育て・児童福祉モデル事業 | `https://www.cfa.go.jp/procurement/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `gov_mhlw` | **厚生労働省** | 生活困窮自立支援・障害者福祉・各種助成金 | `https://www.mhlw.go.jp/stf/boshu/` | `httpx` (RSS/HTML) | XML RSS / `JsonCss` | 1日1回 |
| `gov_maff` | **農林水産省** | 農福連携・農山漁村振興交付金・地域活性化 | `https://www.maff.go.jp/j/supply/hojo/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `gov_env` | **環境省** | 地域脱炭素先行地域・環境保全・ローカルSDGs | `https://www.env.go.jp/guide/boshu/` | `httpx` (RSS/HTML) | XML RSS / `JsonCss` | 1日1回 |
| `gov_mext` | **文部科学省** | 社会教育・地域スポーツ・文化芸術振興 | `https://www.mext.go.jp/b_menu/boshu/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `gov_meti` | **経済産業省** | ソーシャルビジネス・地域商業活性化 (jGrants外) | `https://www.meti.go.jp/information_2/publicoffer/` | `httpx` (RSS/HTML) | XML RSS / `JsonCss` | 1日1回 |
| `gov_cao_janpia` | **内閣府 / JANPIA** | 休眠預金等活用事業・NPO 支援助成 | `https://www.janpia.or.jp/grant/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `gov_cao_chihou` | **内閣府 地方創生推進事務局** | 地方創生交付金・地域活性化モデル事業 | `https://www.chiiki.go.jp/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 週2回 |
| `gov_reconstruction` | **復興庁** | 被災地心のケア・復興支援事業公募 | `https://www.reconstruction.go.jp/topics/main-cat1/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 週2回 |

---

### 2.3 カテゴリ C: 自治体・ローカルニュース・行政施策 (Local Government & Policies)

| Source ID | 情報源名称 | 対象地域 | URL / フィード | 取得手法 | 確定的抽出方式 | 更新頻度 |
|---|---|---|---|---|---|---|
| `gov_toyama_pref_rss` | **富山県新着広報 RSS** | 富山県全域 | `https://www.pref.toyama.jp/rss/shinchaku.xml` | `httpx` (RSS) | XML RSS + markdownify | 6時間ごと |
| `gov_toyama_pref_diff` | **富山県 補助金・公募一覧** | 富山県全域 | `https://www.pref.toyama.jp/shinchaku.html` | `httpx` (HTML) | `JsonCss` (セレクター) | 1日1回 |
| `gov_toyama_city` | **富山市 補助金・助成金情報** | 富山市 | `https://www.city.toyama.lg.jp/boshu/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `gov_takaoka_city` | **高岡市 事業者・NPO支援** | 高岡市 | `https://www.city.takaoka.toyama.jp/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `gov_imizu_city` | **射水市 補助金ポータル** | 射水市 | `https://www.city.imizu.toyama.jp/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `gov_kurobe_city` | **黒部市 公募・補助金** | 黒部市 | `https://www.city.kurobe.toyama.jp/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `gov_tonami_city` | **砺波市 NPO・地域振興** | 砺波市 | `https://www.city.tonami.lg.jp/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `doc_toyama_master_plan` | **富山県 総合計画・予算書 PDF** | 富山県全域 | 各市町村 総合計画PDF/Word | `Crawl4AI × Camoufox` | PDF Normalizer / Surya OCR | 月1回 |

---

### 2.3 カテゴリ C: 民間助成財団・企業 CSR (Private Foundations & Corporate CSR)

| Source ID | 情報源名称 | 運営団体 | 参照 URL | 取得手法 | 確定的抽出方式 | 更新頻度 |
|---|---|---|---|---|---|---|
| `pvt_nippon_foundation` | **日本財団 助成事業** | 公益財団法人 日本財団 | `https://www.nippon-foundation.or.jp/grant_application` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `pvt_akaihane` | **赤い羽根 助成金情報** | 社会福祉法人 中央共同募金会 | `https://www.akaihane.or.jp/subsidies/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1日1回 |
| `pvt_toyota_found` | **トヨタ財団 助成プログラム** | 公益財団法人 トヨタ財団 | `https://www.toyotafound.or.jp/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1週1回 |
| `pvt_canpan` | **CANPAN 助成金データベース** | 日本財団 CANPAN | `https://fields.canpan.info/grant/` | `Crawl4AI × Camoufox` | `JsonCss` (一覧パース) | 1日1回 |
| `pvt_lush_fund` | **LUSH チャリティバンク** | ラッシュジャパン | `https://weare.lush.com/jp/charity-pot/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1週1回 |
| `pvt_matsushita_found` | **パナソニック幸之助記念財団** | パナソニック財団 | `https://matsushita-konosuke-zaidan.or.jp/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1週1回 |
| `pvt_yamato_found` | **ヤマト福祉財団 助成事業** | ヤマト福祉財団 | `https://www.yamato-fukushi.jp/` | `Crawl4AI × Camoufox` | `JsonCss` + markdownify | 1週1回 |

---

### 2.4 カテゴリ D: 市民参加・NPO 内部アセット (Civic & NPO Internal Assets)

| Source ID | 情報源名称 | 提供元 | データ形式 | 連携方式 | 用途 |
|---|---|---|---|---|---|
| `asset_npo_profile` | **NPO 団体実績・プロフィール** | 各登録 NPO 団体 | DB (PostgreSQL / Supabase) | 直近 API / DB クエリ | 申請書自動生成・適合度評価 |
| `civic_quadratic_vote` | **市民合意・二次投票ログ** | Plurality Connect | Quadratic Voting ログ JSON | Supabase Realtime | 提案書エディタのエビデンス自動挿入 |
| `civic_deliberation_thread` | **熟議スレッド・合意形成率** | Plurality Connect | 熟議テキスト / 合意率 % | FastAPI 内部連携 | 政策適合性・市民支持率エビデンス |
| `asset_volunteer_history` | **ボランティア実行・参加実績** | Volunteer Connect | 活動回数・スキルログ DB | DB クエリ | プロジェクト採択シミュレーション |

---

## 3. 情報源別 収集エンジンの割り当てサマリー

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. API 通信層 (httpx)                                                 │
│    ・jGrants API, JFC 助成金ナビ, e-Gov オープンデータ                  │
├────────────────────────────────────────────────────────────────────────┤
│ 2. RSS ポーリング層 (httpx + feedparser)                               │
│    ・富山県広報 RSS, 各自治体 プレスリリース RSS                        │
├────────────────────────────────────────────────────────────────────────┤
│ 3. 統合 Web クローリング層 (Crawl4AI × Camoufox)                        │
│    ・富山県内全15市町村 補助金ページ                                   │
│    ・日本財団, 中央共同募金会, トヨタ財団, CANPAN, LUSH 等 民間財団全般     │
│    ・Cloudflare 保護サイトおよび JavaScript/SPA 描画ページ             │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. プロファイル管理 & ディレクトリ構造

各情報源の DOM セレクター（`DOMProfile`）および JSON 抽出スキーマは、以下のディレクトリ構成でバージョン管理する。

```
docs/
└── source_registry_list.md          # 本仕様書 (マスターリスト)

backend/
└── collectors/
    ├── registry.json                # 各情報源のメタデータ定義 (Source ID, URL, Engine)
    └── profiles/
        ├── gov_toyama_pref.json     # 富山県の DOMProfile (CSSセレクター)
        ├── pvt_nippon_foundation.json # 日本財団の DOMProfile
        ├── pvt_akaihane.json        # 赤い羽根の DOMProfile
        └── pvt_canpan.json          # CANPAN の DOMProfile
```
