# auto-grants-integrated アーキテクチャ設計書

> **Version**: 1.2 (Revised)  
> **更新日**: 2026-07-15  
> **ステータス**: Draft

---

## 1. システム全体構成図

```mermaid
graph TD
    subgraph DataSources["データソース"]
        DS1["jGrants API<br/>(経済産業省)"]
        DS2["富山市 RSS"]
        DS3["富山県 新着ページ<br/>(RSS無し)"]
        DS4["こども家庭庁<br/>交付金ページ (PDF/Excel含む)"]
        DS5["ミラサポplus"]
        DS6["各種助成金団体<br/>サイト"]
    end

    subgraph Collectors["収集レイヤー (深夜帯実行: CLI / FastAPI Background Tasks)"]
        CL1["jGrants Collector<br/>(API定期取得)"]
        CL2["RSS Collector<br/>(feedparser)"]
        CL3["Page-Diff Collector<br/>(ハッシュ比較 + BeautifulSoup + LLM抽出)"]
        CL4["Cascade Tracker<br/>(PDF/Excel抽出 + 交付金リンク追跡)"]
        CL5["Web Crawler<br/>(汎用クローラー)"]
    end

    subgraph Processing["処理レイヤー"]
        PR1["正規化・構造化<br/>(スキーマ統一)"]
        PR2["LLM意味的抽出<br/>(Claude / GPT)"]
        PR3["重複排除・突合 (データハッシュUpsert)"]
        PR4["知識チャンク分割"]
    end

    subgraph ModalAI["AI / Embedding レイヤー (開発・本番共通: Modal GPU)"]
        AI1["Qwen3-Embedding-8B<br/>(4096dim)"]
        AI2["BgeReranker v2-m3<br/>(Cross-Encoder)"]
    end

    subgraph DataLayer["データレイヤー"]
        DB["PostgreSQL (Supabase)<br/>+ pgvector (HNSW Index: 4096次元固定)"]
    end

    subgraph AppLayer["アプリケーションレイヤー"]
        API["FastAPI Backend<br/>(Python 3.10+)"]
        MCP["MCP Gateway<br/>(67+ tools)"]
        FE["React Frontend<br/>(Vite + TanStack Query)"]
    end

    subgraph Interface["インターフェースレイヤー"]
        IF1["Claude Desktop<br/>/ Claude Code"]
        IF2["Slack / LINE<br/>通知"]
        IF3["Web UI<br/>(React)"]
    end

    DS1 --> CL1
    DS2 --> CL2
    DS3 --> CL3
    DS4 --> CL4
    DS5 --> CL5
    DS6 --> CL5

    CL1 --> PR1
    CL2 --> PR1
    CL3 --> PR2
    CL4 --> PR3
    CL5 --> PR1

    PR1 --> PR4
    PR2 --> PR4
    PR3 --> PR4

    PR4 --> AI1
    AI1 --> DB
    DB --> AI2

    DB --> API
    API --> MCP
    API --> FE

    MCP --> IF1
    API --> IF2
    FE --> IF3

    AI1 -.->|"embed query"| MCP
    AI2 -.->|"rerank results"| MCP

    style ModalAI fill:#1a1a2e,stroke:#e94560,color:#fff
    style DataSources fill:#16213e,stroke:#0f3460,color:#fff
    style Collectors fill:#1a1a2e,stroke:#533483,color:#fff
    style Processing fill:#1a1a2e,stroke:#e94560,color:#fff
    style DataLayer fill:#0f3460,stroke:#53a8b6,color:#fff
    style AppLayer fill:#16213e,stroke:#53a8b6,color:#fff
    style Interface fill:#16213e,stroke:#e94560,color:#fff
```

---

## 2. レイヤー構成の設計方針

| レイヤー | 役割 | 実装方針 |
|---|---|---|
| **DataSources** | 外部データソースとの接続ポイント | 各ソースの特性（API/RSS/HTML/PDF/Excel）に応じた個別コレクター。 |
| **Collectors** | データの取得・初期加工 | **CLIスクリプト**（深夜のcron/GitHub Actions実行用）または **FastAPIの非同期バックグラウンドタスク**。 |
| **Processing** | 正規化・構造化・重複排除 | 統一スキーマへの変換、HTMLタグ等の不要ノイズ除去、個別データ（タイトル等）のハッシュ値を用いたUpsert重複排除。 |
| **AI/Embedding** | ベクトル埋め込み・リランキング | **開発・本番共通**でModal上の Qwen3-8B + BgeReranker を実行。 |
| **Data** | 永続化・ベクトル検索 | PostgreSQL (Supabase) + pgvector。開発・本番共通で **4096次元のHNSWインデックス** に固定。 |
| **Application** | ビジネスロジック・API | FastAPI + MCP Gateway。Modal APIのエラー時はキーワード検索へ自動フォールバック。 |
| **Interface** | ユーザー接点 | Claude Desktop/Code (MCP)、Slack/LINE (通知)、React (Web UI)。 |

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

## 5. ベクトル次元マイグレーション設計

`EMBEDDING_PROVIDER` を何らかの理由で切り替える場合、データベースのカラム次元不一致によるエラーを防ぐため、起動時に次元数セーフティチェックを実行する。

1. **セーフティチェック**: 起動時にDB上の `knowledge_chunks.embedding` 次元数をSQLクエリで取得し、`.env` の `EMBEDDING_DIMENSIONS` (デフォルト4096) と一致しない場合は起動を抑止する。
2. **Re-embedding CLI**: 開発環境から本番環境への移行時など、手動で再埋め込みを行うためのユーティリティを提供（`rebuild_embeddings.py`）。
