# auto-grants-integrated アーキテクチャ設計書

> **Version**: 2.0 (Integrated)  
> **更新日**: 2026-07-15  
> **ステータス**: Draft

---

## 1. システム全体構成図

```mermaid
graph TD
    %% 1. 情報源 (Information Sources)
    subgraph Info_Sources ["情報源 (Information Sources)"]
        GovPlan["行政文書 / 総合計画 (PDF/Word)"]
        GovRFP["公募仕様書 (RFP)"]
        JFC_API["助成金ナビ (JFC API)"]
        WebDOM["民間財団Webサイト (HTML/DOM)"]
        LocalRSS["富山ローカルRSS / 自治体HP"]
        DelibVote["市民合意・投票データ (Quadratic Voting)"]
        NpoAsset["団体アセット (活動実績・プロフィール)"]
    end

    %% 2. 収集・正規化パイプライン (Ingest & Normalization)
    subgraph Ingest_Pipeline ["インジェスト・正規化パイプライン (uv / Python)"]
        Collector["コレクター群 (jGrants API, Tree Crawler, Playwright, RSS Watcher)"]
        Normalizer["Document Normalizer<br/>(PDF/Office/HTML -> Text)"]
        QGate["クオリティゲート (Coverage >= 0.95)"]
        SelfHealing["LLM 自己修復 (learn_profile / repair_extract)"]
        
        Collector --> Normalizer
        Normalizer --> QGate
        QGate -- 失敗 --> SelfHealing
        SelfHealing --> Collector
    end

    %% 3. データストア & ナレッジ化
    subgraph Data_Store_Knowledge ["データストア & ナレッジ化"]
        PG[("PostgreSQL 15 + pgvector")]
        ModalGPU["Modal GPU Serverless<br/>(Qwen3-Embedding / Reranker)"]
        GraphRAG["GraphRAG (政策・施策構造化)"]
        
        QGate -- 合格/登録 --> PG
        QGate -- 合格/構造化 --> GraphRAG
        GraphRAG --> PG
        PG <--> ModalGPU
    end

    %% 4. 統合バックエンド (FastAPI)
    subgraph Backend_Space ["統合バックエンド (FastAPI / auto-grantsv2 ベース)"]
        Main["FastAPI Entry (main.py)"]
        
        subgraph Subsidies_Domain ["助成金ドメイン"]
            S_Router["routes.py (検索・一覧)"]
            S_Rep["db.py (助成金リポジトリ)"]
        end

        subgraph GovPro_Domain ["提案書生成・シミュレーションドメイン"]
            P_Gen["proposal_generator.py (ProposalGenerator)"]
            P_Sim["採択シミュレーター (Simulate API)"]
        end
        
        Main --> S_Router
        Main --> P_Gen
        Main --> P_Sim
        
        S_Router --> S_Rep
        S_Rep --> PG
        
        P_Gen -->|団体アセット取得| PG
        P_Gen -->|政策適合エビデンス検索| GraphRAG
        P_Sim -->|RFP適合度判定| PG
    end

    %% 5. フロントエンド (React 19)
    subgraph Client_Space ["フロントエンド (Vite / React 19 / TS)"]
        Dashboard["ダッシュボード (Active Grants, タイムライン)"]
        Sankey["予算フロー可視化 (Sankey)"]
        Globe["グローバルマップ (Globe)"]
        GovProUI["提案書エディタ (根拠付き自動生成)"]
        
        ClientAPI["API クライアント層<br/>(自動生成: Query / Zod / SDK)"]
        
        Dashboard --> ClientAPI
        Sankey --> ClientAPI
        Globe --> ClientAPI
        GovProUI --> ClientAPI
    end

    %% 外部連携 & 同期
    ClientAPI -->|HTTPS / JSON| Main
    Info_Sources --> Collector
    DelibVote --> PG
    NpoAsset --> PG
```

---

## 2. レイヤー構成の設計方針

| レイヤー | 役割 | 統合・実装方針 |
|---|---|---|
| **GrantSources** | 助成金および直接的資金調達元 | 従来の公募情報に加え、市民や企業からのクラウドファンディングの寄付フロー（リアルタイムトランザクション）を統合。 |
| **BudgetSources** | 国・自治体の財務情報 | 財務省等の予算PDFのほか、`zaisei-radar`が使用する自治体の財務データCSVをインポート。 |
| **Collectors** | データ収集・同期 | 日次/週次バッチスクリプトに加え、ボランティア応募やクラファン決済時のSupabase Realtimeリスナーによる即時反映。 |
| **Processing** | データ構造化・エージェント | `BeautifulSoup` + `LLM` によるテキスト抽出。自治体基本計画のGraphRAGコミュニティ生成。プロソーシャル（市民合意）指標の算出。 |
| **AI/Embedding** | セマンティック処理 | Modal GPU上のQwen3 (4096次元) で統一。BgeRerankerによる高精度リランキング。 |
| **Security & Privacy** | 信頼・プライバシー保護 | W3C規格の **DID（分散型ID）** を用いた自己主権型認証、および **zk-SNARKs** を用いた個人情報不要の実績・住民属性証明。 |
| **Data** | 永続化とリアルタイム連携 | PostgreSQL (Supabase) 上に、助成金、予算フロー、クラファン、プロジェクト（ボランティア）、市民投票、団体アセット、およびZKP検証用資格（VC）の全テーブルを一元化。Row Level Security (RLS) によるセキュリティ確保。 |
| **Application** | ロジック・インターフェース | FastAPIによるAIエージェントのオーケストレーション、ZKP検証エンジン、外部接続用MCPサーバー。 |
| **Frontend** | ユーザーインターフェース | React 19 + Vite。「Cosmic Glass」デザインシステムを採用した、左固定多階層ナビゲーションによるSPA。 |
| **Interface** | マルチチャネルアクセス | Web UI、Slack/LINE/Emailによる期日・応募通知、Claudeによる自然言語MCP操作。 |

---

## 3. 統合データフロー設計

### 3.1 市民合意・エビデンス付きプロポーザル生成フロー
市民からの合意と実績データを、自治体への提案書（プロポーザル）にエビデンスとしてシームレスに組み込むデータフロー。

```mermaid
sequenceDiagram
    participant Citizen as 市民 (Web UI)
    participant Delib as 協議・投票DB
    participant Fact as 団体アセットDB
    participant GraphRAG as GraphRAG (GovPro)
    participant Writer as 執筆エージェント
    participant Proposal as 提案書 (PDF/MD)

    Citizen->>Delib: プロジェクト案に投票 (Quadratic Voting)
    Delib->>Delib: 合意率・統計データを集計 (例: 合意率 89%)
    Fact->>Fact: 過去のボランティア活動実績を登録
    GraphRAG->>GraphRAG: 自治体の総合計画・施策を分析
    Writer->>Delib: 市民合意データ・投票ログを読み込み
    Writer->>Fact: 活動実績・参加メンバー数を読み込み
    Writer->>GraphRAG: 政策適合性のエビデンスを取得
    Writer->>Writer: 提案書の下書きとエビデンスを自動生成
    Writer-->>Proposal: 根拠（出典）付き提案書をエクスポート

### 3.2 ゼロ知識証明 (ZKP) を用いたプライバシー保護型属性証明フロー
市民が機微な個人データ（本名、住所、過去の個別の被支援者情報）を開示せずに、「住民資格」や「活動実績」のみを安全に検証者に証明する流れ。

```mermaid
sequenceDiagram
    participant Wallet as シビック・ウォレット (WASM)
    participant CDN as パラメータ配信 (CDN)
    participant Issuer as 発行者 (NPO/自治体)
    participant DB as Supabase (ZKP / VCストア)
    participant Verifier as 検証者 (自治体/AI審査官)

    %% 資格発行フェーズ
    Issuer->>Wallet: ボランティア実績 / 住民権の暗号署名データ(VC)を発行
    Wallet->>Wallet: ウォレット（ブラウザ）へ実績の生データを安全に格納

    %% 証明・検証フェーズ (初期化含む)
    Note over Wallet, CDN: [初期化] 証明生成に必要な回路ファイル (.wasm) と証明鍵 (.zkey) をロード
    Wallet->>CDN: 回路パラメータ (proving_key.zkey) をリクエスト
    CDN-->>Wallet: パラメータデータを返却・キャッシュ
    Wallet->>Wallet: 生データと proving_key から「合計活動時間 >= 50」のゼロ知識証明(zk_proof)を生成 (Groth16)
    Wallet->>DB: zk_proof と commitment_hash を登録
    
    %% 検証フェーズ
    Note over Verifier, CDN: [初期化] 検証キー (verification_key.json) をロード
    Verifier->>DB: ユーザーの zk_proof と commitment_hash をロード
    Verifier->>Verifier: 検証キーと公開シグナルを用いて ZKP の正当性を検証 (生データは見えない)
    Verifier-->>Verifier: 検証成功 (属性が真であることを承認)
```

```

---

## 4. フロントエンド UI アーキテクチャ

プラットフォーム全体は、左側に固定された「Cosmic Glass」スタイルの多階層ナビゲーションからアクセスします。従来の4つのタブを廃止し、3つの大セクション、12の機能モジュールへ整理・再設計します。

```mermaid
graph LR
    NAV["左固定サイドバー (Cosmic Glass)"]

    subgraph Section1["💰 資金と財政"]
        NAV --> SB_DASH["🏠 ホーム"]
        NAV --> SB_FLOW["📊 予算フロー"]
        NAV --> SB_APPS["🔍 助成金マッチング"]
        NAV --> SB_CF["🪙 クラウドファンディング"]
    end

    subgraph Section2["🤖 自治体提案"]
        NAV --> SB_DOCS["📂 行政資料ライブラリ"]
        NAV --> SB_PROP["🤖 提案書ジェネレーター"]
        NAV --> SB_FLOW_MAP["🌐 政策接続フロー"]
    end

    subgraph Section3["👥 市民参加と実行"]
        NAV --> SB_DELIB["🗳️ 市民合意・投票"]
        NAV --> SB_PROJ["🤝 ボランティア案件"]
        NAV --> SB_ORG["🏢 団体アセット・メンバー"]
    end

    style NAV fill:#080c14,stroke:#6366f1,color:#fff
    style Section1 fill:#0f132a,stroke:#10b981,color:#fff
    style Section2 fill:#0f132a,stroke:#6366f1,color:#fff
    style Section3 fill:#0f132a,stroke:#06b6d4,color:#fff
```

### 4.1 共通デザインシステム仕様 (Cosmic Glass)

全コンポーネントは、`index.css` にて一元管理される以下のデザイントークンに従って描画されます。

| トークン名 | 値 | 用途 |
|---|---|---|
| `--bg-cosmic` | `linear-gradient(135deg, #060913 0%, #0F132A 100%)` | アプリケーション全体の背景色 |
| `--surface-glass` | `rgba(15, 20, 38, 0.45)` | カード、モーダル、サイドバー等の磨りガラス背景 |
| `--border-glass` | `rgba(255, 255, 255, 0.06)` | パネルの極細境界線（1px） |
| `--blur-glass` | `blur(16px)` | 背景の磨りガラス（ぼかし）フィルター (パネル用) |
| `--blur-glass-md` | `blur(12px)` | 背景の磨りガラス（ぼかし）フィルター (カード用) |
| `--blur-glass-sm` | `blur(8px)` | 背景の磨りガラス（ぼかし）フィルター (ボタン用) |
| `--color-primary` | `#6366F1` | プライマリ・AI・ジェネレーター用アクセント (Cosmic) / Terracotta (Nordic) |
| `--color-accent` | `#10B981` | 資金・適合・正常ステータス用アクセント (Cosmic) / Sage Green (Nordic) |
| `--color-civic` | `#06B6D4` | 市民参加・Plurality・合意形成用アクセント (Cosmic) / Dusty Gold (Nordic) |
| `--color-alert` | `#EF4444` | アラート・締切・エラー用アクセント (Cosmic) / Nordic Red (Nordic) |
| `--font-display` | `Outfit` | タイトル、見出し、ヘッダー |
| `--font-sans` | `Inter` | 本文、ログ、コード表示 |

### 4.2 主要ライブラリの役割分担

* **グラフ可視化**:
  * **サンキーダイアグラム**: `@nivo/sankey` (国庫→自治体→採択者までの資金フロー)
  * **レーダーチャート/進捗チャート**: `recharts` (期待値評価、クラファン支援進捗)
  * **ネットワークグラフ**: `d3-force` または `react-force-graph` (GraphRAGの施策マップ)
* **リアルタイム通信**:
  * `@supabase/supabase-js` (Realtime機能を用いた投票集計、ボランティア応募、メッセージ同期)
