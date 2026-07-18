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
        PG[("PostgreSQL 15 + pgvector<br>(助成金・予算・投票・ボランティア・ZKPデータ)")]
        Realtime["Supabase Realtime Engine<br>(クラファン決済・応募の即時通知)"]
        ModalGPU["Modal GPU Serverless<br/>(Qwen3-Embedding / Reranker / スキルマッチング)"]
        GraphRAG["GraphRAG (政策・施策構造化)"]
        
        QGate -- 合格/登録 --> PG
        QGate -- 合格/構造化 --> GraphRAG
        GraphRAG --> PG
        PG <--> ModalGPU
        PG --> Realtime
    end

    %% 4. 統合バックエンド (FastAPI)
    subgraph Backend_Space ["統合バックエンド (FastAPI / auto-grantsv2 ベース)"]
        Main["FastAPI Entry (main.py)"]
        
        subgraph Subsidies_Domain ["助成金・予算ドメイン"]
            S_Router["routes.py (検索・一覧)"]
            S_Rep["db.py (助成金リポジトリ)"]
        end

        subgraph GovPro_Domain ["提案書生成・シミュレーションドメイン"]
            P_Gen["proposal_generator.py (ProposalGenerator)"]
            P_Sim["採択シミュレーター (Simulate API)"]
        end

        subgraph Plurality_Domain ["市民参加 (Plurality) ドメイン"]
            P_Vote["quadratic_voting.py (二次投票処理)"]
            P_Thread["deliberation.py (合意形成スレッド)"]
        end

        subgraph Action_Domain ["実行・資金調達ドメイン"]
            A_Vol["volunteer_service.py (スキルマッチング)"]
            A_CF["crowdfunding.py (クラファン・資金監査)"]
        end

        subgraph Security_Domain ["認証・ZKP・DID ドメイン"]
            Sec_Auth["did_resolver.py (DID/VC検証・バッジ発行)"]
            Sec_ZKP["zk_proof_verifier.py (zk-SNARKs検証)"]
        end

        MCP_GW["MCP Gateway (Stdio/HTTP)<br>(67+ LLMツール公開)"]
        
        Main --> S_Router
        Main --> P_Gen
        Main --> P_Sim
        Main --> Plurality_Domain
        Main --> Action_Domain
        Main --> Security_Domain
        Main --> MCP_GW
        
        S_Router --> S_Rep
        S_Rep --> PG
        
        P_Gen -->|団体アセット取得| PG
        P_Gen -->|政策適合エビデンス検索| GraphRAG
        P_Sim -->|RFP適合度判定| PG
        
        Plurality_Domain --> PG
        Action_Domain --> PG
        Security_Domain --> PG
    end

    %% 5. 外部連携サービス
    subgraph External_Services ["外部連携サービス"]
        SupabaseAuth["Supabase Auth (JWT認証)"]
        StripeAPI["Stripe API (寄付決済)"]
        FCM["Firebase Cloud Messaging<br>(OS標準プッシュ通知)"]
    end

    %% 6. フロントエンド (React 19)
    subgraph Client_Space ["フロントエンド (Vite / React 19 / TS)"]
        subgraph Web_Client ["PC用 Web画面"]
            Dashboard["ダッシュボード (Active Grants, タイムライン)"]
            Sankey["予算フロー可視化 (Sankey + 資金監査)"]
            Globe["グローバルマップ (Globe)"]
            GovProUI["提案書エディタ (根拠付き自動生成)"]
            MobileBanner["アプリ移行促進バナー (モバイルWebアクセス時)"]
        end

        subgraph Mobile_App ["モバイル用アプリ (PWA / Capacitor)"]
            CivicUI["協議・投票 (Quadratic Voting)"]
            VolunteerUI["ボランティア (スキルマッチング)"]
            WalletUI["シビック・ウォレット (秘密鍵・オープンバッジ管理)"]
            ZKP_WASM["zk-SNARKs Prover (Client WASM)"]
            
            CivicUI --> ZKP_WASM
            VolunteerUI --> WalletUI
        end

        ClientAPI["API クライアント層<br/>(自動生成: Query / Zod / SDK)"]
        SecureStore["デバイスセキュアストレージ (Keystore / Secure Enclave)"]

        Dashboard --> ClientAPI
        Sankey --> ClientAPI
        Globe --> ClientAPI
        GovProUI --> ClientAPI
        
        CivicUI --> ClientAPI
        VolunteerUI --> ClientAPI
        WalletUI --> SecureStore
    end

    %% 外部連携 & 同期
    ClientAPI -->|HTTPS / JSON| Main
    Realtime -->|WebSocket| ClientAPI
    Dashboard -.->|非同期プリウォーム| ModalGPU
    Main --> SupabaseAuth
    Action_Domain --> StripeAPI
    Security_Domain --> FCM
    ZKP_WASM -->|ZKP証明書送信| Security_Domain
    Info_Sources --> Collector
    DelibVote --> PG
    NpoAsset --> PG
```

---

## 2. レイヤー構成の設計方針

| レイヤー (図中サブグラフ名) | 役割 | 統合・実装方針 |
|---|---|---|
| **Info_Sources** (情報源) | 助成金・予算・行政文書・市民合意の原データ | jGrants API、民間財団Web/DOM、自治体RSS、行政文書PDF/Word、RFP、市民投票データ、団体実績を包含。 |
| **Ingest_Pipeline** (インジェスト・正規化) | 収集・正規化・品質検証 | コレクター群（jGrants API / Tree Crawler / Playwright / RSS Watcher）→ Document Normalizer → クオリティゲート（Coverage ≧ 0.95）。失敗時は LLM 自己修復ループで自動復旧。 |
| **Data_Store_Knowledge** (データストア & ナレッジ化) | 永続化・ベクトル化・構造化・リアルタイム配信 | PostgreSQL 15 + pgvector に全テーブルを一元管理。GraphRAG で政策・施策を知識グラフ化。Supabase Realtime Engine でクラファン決済・ボランティア応募を即時にフロントへ配信。Modal GPU (Qwen3-Embedding / BgeReranker) でセマンティック検索・スキルマッチングを実行。RLS によるセキュリティ確保。 |
| **Backend_Space** (統合バックエンド) | ドメインロジック・API・MCP | FastAPI + uv。助成金・予算 / 提案書生成・シミュレーション / 市民参加 (Plurality) / 実行・資金調達 / 認証・ZKP・DID の5ドメインに分割。**MCP Gateway (Stdio/HTTP)** で全APIを67+のLLMツールとして公開し、Claude等からの自然言語操作を実現。 |
| **External_Services** (外部連携) | 認証・決済・プッシュ通知 | Supabase Auth (JWT)、Stripe API (寄付決済)、Firebase Cloud Messaging (OS標準プッシュ通知)。 |
| **Client_Space** (フロントエンド) | PC Web + モバイルアプリ | React 19 + Vite + TypeScript。PC用 Web画面（ダッシュボード、Sankey、Globe、提案書エディタ）と、モバイル用アプリ (PWA / Capacitor: 協議・投票、ボランティア、シビック・ウォレット、zk-SNARKs Prover WASM) に論理分割。Hey API による型・SDK・Zod の完全自動生成。 |

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
