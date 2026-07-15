# auto-grants-integrated アーキテクチャ設計書

> **Version**: 1.3 (Revised)  
> **更新日**: 2026-07-15  
> **ステータス**: Draft

---

## 1. システム全体構成図

```mermaid
graph TD
    subgraph GrantSources["助成金データソース"]
        DS1["jGrants API<br/>(経済産業省)"]
        DS2["富山市 RSS"]
        DS3["富山県 新着ページ<br/>(RSS無し)"]
        DS4["こども家庭庁<br/>交付金ページ (PDF/Excel含む)"]
        DS5["ミラサポplus"]
        DS6["各種助成金団体<br/>サイト"]
    end

    subgraph BudgetSources["予算データソース (moneyflow-visualizer統合)"]
        BS1["財務省<br/>一般会計予算 (PDF/Excel)"]
        BS2["厚生労働省<br/>社会保障関係予算"]
        BS3["こども家庭庁<br/>予算概要"]
        BS4["IMF DOTS<br/>World Bank API"]
        BS5["日本銀行<br/>資金循環統計"]
    end

    subgraph Collectors["収集レイヤー (深夜帯実行: CLI / FastAPI Background Tasks)"]
        CL1["jGrants Collector<br/>(API定期取得)"]
        CL2["RSS Collector<br/>(feedparser)"]
        CL3["Page-Diff Collector<br/>(ハッシュ比較 + BeautifulSoup + LLM抽出)"]
        CL4["Cascade Tracker<br/>(PDF/Excel抽出 + 交付金リンク追跡)"]
        CL5["Web Crawler<br/>(汎用クローラー)"]
        CL6["Budget Fetcher<br/>(予算PDF/API収集)"]
    end

    subgraph Processing["処理レイヤー"]
        PR1["正規化・構造化<br/>(スキーマ統一)"]
        PR2["LLM意味的抽出<br/>(Claude / GPT)"]
        PR3["重複排除・突合 (データハッシュUpsert)"]
        PR4["知識チャンク分割"]
        PR5["予算フローグラフ生成<br/>(nodes/edges構築)"]
    end

    subgraph ModalAI["AI / Embedding レイヤー (開発・本番共通: Modal GPU)"]
        AI1["Qwen3-Embedding-8B<br/>(4096dim)"]
        AI2["BgeReranker v2-m3<br/>(Cross-Encoder)"]
    end

    subgraph DataLayer["データレイヤー"]
        DB["PostgreSQL (Supabase)<br/>+ pgvector (HNSW Index: 4096次元固定)<br/>+ nodes/edges (予算フローグラフ)"]
    end

    subgraph AppLayer["アプリケーションレイヤー"]
        API["FastAPI Backend<br/>(Python 3.10+)"]
        MCP["MCP Gateway<br/>(67+ tools)"]
    end

    subgraph FrontendLayer["フロントエンドレイヤー (React + Vite)"]
        FE_NAV["タブナビゲーション<br/>Dashboard / Money Flow<br/>Applications / Settings"]
        FE_DASH["ダッシュボード<br/>(タイムライン + 新着カード<br/>+ AIステータス)"]
        FE_FLOW["予算フロー可視化<br/>(@nivo/sankey + react-globe.gl)"]
        FE_APP["助成金一覧・詳細<br/>(テーブル + 適合判定モーダル<br/>+ レーダーチャート)"]
    end

    subgraph Interface["インターフェースレイヤー"]
        IF1["Claude Desktop<br/>/ Claude Code"]
        IF2["Slack / LINE<br/>通知"]
        IF3["Web UI<br/>(グラスモフィズム<br/>ダークテーマ)"]
    end

    DS1 --> CL1
    DS2 --> CL2
    DS3 --> CL3
    DS4 --> CL4
    DS5 --> CL5
    DS6 --> CL5

    BS1 --> CL6
    BS2 --> CL6
    BS3 --> CL6
    BS4 --> CL6
    BS5 --> CL6

    CL1 --> PR1
    CL2 --> PR1
    CL3 --> PR2
    CL4 --> PR3
    CL5 --> PR1
    CL6 --> PR5

    PR1 --> PR4
    PR2 --> PR4
    PR3 --> PR4
    PR5 --> DB

    PR4 --> AI1
    AI1 --> DB
    DB --> AI2

    DB --> API
    API --> MCP
    API --> FE_NAV

    FE_NAV --> FE_DASH
    FE_NAV --> FE_FLOW
    FE_NAV --> FE_APP

    MCP --> IF1
    API --> IF2
    FE_NAV --> IF3

    AI1 -.->|"クエリ埋め込み"| MCP
    AI2 -.->|"リランキング"| MCP
    DB -.->|"予算フローデータ"| FE_FLOW

    style ModalAI fill:#1a1a2e,stroke:#e94560,color:#fff
    style GrantSources fill:#16213e,stroke:#0f3460,color:#fff
    style BudgetSources fill:#16213e,stroke:#5e5ce6,color:#fff
    style Collectors fill:#1a1a2e,stroke:#533483,color:#fff
    style Processing fill:#1a1a2e,stroke:#e94560,color:#fff
    style DataLayer fill:#0f3460,stroke:#53a8b6,color:#fff
    style AppLayer fill:#16213e,stroke:#53a8b6,color:#fff
    style FrontendLayer fill:#162032,stroke:#30d158,color:#fff
    style Interface fill:#16213e,stroke:#e94560,color:#fff
```

---

## 2. レイヤー構成の設計方針

| レイヤー | 役割 | 実装方針 |
|---|---|---|
| **GrantSources** | 助成金・補助金データソースとの接続ポイント | 各ソースの特性（API/RSS/HTML/PDF/Excel）に応じた個別コレクター。 |
| **BudgetSources** | 国・自治体の予算データソース | 財務省/厚労省/こども家庭庁の予算PDF/Excel、IMF DOTS/World Bank API、日本銀行資金循環統計などの公式データソース。moneyflow-visualizerの10種のデータソースを継承。 |
| **Collectors** | データの取得・初期加工 | **CLIスクリプト**（深夜のcron/GitHub Actions実行用）または **FastAPIの非同期バックグラウンドタスク**。予算データ用の Budget Fetcher を新設。 |
| **Processing** | 正規化・構造化・重複排除 | 統一スキーマへの変換、HTMLタグ等の不要ノイズ除去、個別データのハッシュ値を用いたUpsert重複排除。予算データは nodes/edges グラフ構造へ変換。 |
| **AI/Embedding** | ベクトル埋め込み・リランキング | **開発・本番共通**でModal上の Qwen3-8B + BgeReranker を実行。 |
| **Data** | 永続化・ベクトル検索・グラフデータ | PostgreSQL (Supabase) + pgvector (4096次元HNSW)。助成金データ (grants) と予算フローグラフ (nodes/edges) を一元管理。 |
| **Application** | ビジネスロジック・API | FastAPI + MCP Gateway。Modal APIのエラー時はキーワード検索へ自動フォールバック。 |
| **Frontend** | ビュー切り替え・UI | React + Vite。タブナビ (Dashboard / Money Flow / Applications / Settings) によるビュー切替。グラスモフィズムダークテーマを統一適用。 |
| **Interface** | ユーザー接点 | Claude Desktop/Code (MCP)、Slack/LINE (通知)、Web UI (グラスモフィズムダークテーマ)。 |

---

## 3. Embeddingプロバイダアーキテクチャ

本プロジェクトでは、開発環境・本番環境ともに `EMBEDDING_PROVIDER=modal` を基本構成とすることで、データベースのベクトル次元数（4096次元）を統一し、環境間での不整合（スキーマ変更エラー）を排除する。

```
[開発・本番環境共通]
EMBEDDING_PROVIDER=modal 
  └─► ModalEmbeddingService ──► Modal API (Qwen3-8B, 4096dim) ──► PostgreSQL vector(4096)

[オフライン/テスト用フォールバック]
EMBEDDING_PROVIDER=mock (または none)
  └─► MockEmbeddingService ──► 極小のランダムノイズを含む4096次元ベクトルを返却してゼロ除算とDBエラーを回避
```

---

## 4. データフロー設計

### 4.1 助成金収集 & ベクトル埋め込みフロー

```mermaid
sequenceDiagram
    participant Cron as Cron / GitHub Actions
    participant Collector as Collector
    participant Processor as Processing Layer
    participant Modal as Modal GPU<br/>(Qwen3-8B)
    participant DB as PostgreSQL<br/>(pgvector)

    Cron->>Collector: 深夜のスケジュール起動
    Collector->>Collector: データソースから取得 (PDF/Excel含む)
    Collector->>Processor: 生データ送信
    Processor->>Processor: BeautifulSoupによるHTMLクレンジング / 添付ファイルテキスト抽出
    Processor->>Processor: 新規データハッシュ生成と重複チェック
    Processor->>Modal: テキストチャンク送信 (POST /embed)
    Modal->>Modal: Qwen3-8B推論 (4096dim)
    Modal-->>Processor: ベクトル配列返却
    Processor->>DB: チャンク + ベクトル保存 (knowledge_chunks)
    Processor->>DB: 助成金データUpsert (grants)
```

(セマンティック検索フローは前バージョンと同様のため省略)

---

## 5. フロントエンド UI アーキテクチャ

ユーザーはメインナビゲーション（タブ）でビューを切り替え、異なる視点から予算・助成金データにアクセスする。全ビューは共通の「ダークインディゴ＋グラスモフィズム」デザインシステム（`index.css`）で構築する。

```mermaid
graph LR
    NAV["メインナビゲーション"]

    NAV --> DASH["📊 Dashboard<br/>総合サマリー"]
    NAV --> FLOW["💰 Money Flow<br/>予算可視化"]
    NAV --> APPS["📄 Applications<br/>助成金一覧・詳細"]
    NAV --> SETS["⚙️ Settings"]

    DASH --> D1["予算カレンダータイムライン"]
    DASH --> D2["新着助成金カード<br/>(マッチスコア付き)"]
    DASH --> D3["AIエージェント<br/>ステータスウィジェット"]

    FLOW --> F1["サンキーダイアグラム<br/>(国庫→省庁→助成金→受給者)"]
    FLOW --> F2["3D地球儀<br/>(国際資金フロー)"]

    APPS --> A1["助成金一覧テーブル<br/>(フィルタリング・ソート)"]
    APPS --> A2["適合判定モーダル<br/>(Quote/出典紐付け)"]
    APPS --> A3["4軸レーダーチャート<br/>(期待値評価)"]
    APPS --> A4["申請書プレビュー/DL"]

    style NAV fill:#0b0f19,stroke:#5e5ce6,color:#fff
    style DASH fill:#162032,stroke:#30d158,color:#fff
    style FLOW fill:#162032,stroke:#5e5ce6,color:#fff
    style APPS fill:#162032,stroke:#30d158,color:#fff
    style SETS fill:#162032,stroke:#64748b,color:#fff
```

### 5.1 デザインシステム

全ビューに共通するデザイントークンは `index.css` の CSS カスタムプロパティで定義する。

| トークン | 値 | 用途 |
|---|---|---|
| `--bg-gradient-start` | `#0b0f19` | 背景グラデーション開始 |
| `--bg-gradient-end` | `#162032` | 背景グラデーション終了 |
| `--surface-glass` | `rgba(22,32,50,0.45)` | グラスモフィズムカード/パネル背景 |
| `--color-primary` | `#5e5ce6` | エレクトリックインディゴ (ボタン・アクティブタブ) |
| `--color-accent-mint` | `#30d158` | ネオンミント (マッチスコア・正常ステータス) |
| `--font-display` | `Outfit` | ヘッダー・タイトル |
| `--font-sans` | `Inter` | 本文テキスト |

### 5.2 主要コンポーネント一覧

| コンポーネント | ライブラリ | ビュー |
|---|---|---|
| サンキーダイアグラム | `@nivo/sankey` | Money Flow |
| 3D地球儀 | `react-globe.gl` + `three` | Money Flow |
| レーダーチャート | `recharts` | Applications 詳細モーダル |
| タイムライン/ガント | カスタム実装 | Dashboard |
| データフェッチ | TanStack Query | 全ビュー共通 |

---

## 6. ベクトル次元マイグレーション設計

`EMBEDDING_PROVIDER` を何らかの理由で切り替える場合、データベースのカラム次元不整合によるエラーを防ぐため、起動時に次元数セーフティチェックを実行する。

1. **セーフティチェック**: 起動時にDB上の `knowledge_chunks.embedding` 次元数をSQLクエリで取得し、`.env` の `EMBEDDING_DIMENSIONS` (デフォルト4096) と一致しない場合は起動を抑止する。
2. **Re-embedding CLI**: 開発環境から本番環境への移行時など、手動で再埋め込みを行うためのユーティリティを提供（`rebuild_embeddings.py`）。
