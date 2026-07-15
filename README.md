# auto-grants-integrated

市民活動団体（NPO・市民団体）と自治体、および財政データを結びつけ、助成金の自動選定から申請・プロジェクト実行・市民合意・資金調達までをワンストップで支援する統合プラットフォームです。

## 🌟 プロジェクト概要

本プロジェクトは、以下のモジュール群を統合し、市民協働の活性化と予算フローの透明化を目指すWebアプリケーションです。

1.  **助成金データ収集・管理 (`auto-grantsv2` & `Subsidy Radar`)**
    *   富山県・富山市などの新着RSS/ページ差分クローリング、およびjGrants APIとの同期。
    *   LLMを用いた公募情報の自動構造化と重複排除。
2.  **予算フロー可視化 (`moneyflow-visualizer` & `zaisei-radar`)**
    *   国・省庁・事業から地方自治体の助成金に至る資金の流れを、予算データ（PDF/Excel）から解析してグラフ化。
    *   `@nivo/sankey` とエッジパーティクルアニメーションを用いた流動的なサンキーダイアグラム可視化。
3.  **AI適合判定・マッチング (Modal AIインフラ)**
    *   Modal GPU上で稼働する `Qwen3-Embedding-8B` と `BgeReranker v2-m3` による4096次元セマンティック検索。
    *   団体のプロファイルと助成金要件のクロスエンコーダ・リランキングによるAI適合判定。
    *   Modal起動待ち（コールドスタート）時の PostgreSQL `pg_trgm` トリグラム類似度によるローカルフォールバック検索。
4.  **エビデンス付き提案書生成 (`govdoc-app` / `GovPro`)**
    *   自治体総合計画等の行政文書を GraphRAG で解析。
    *   行政の掲げる目標と自団体の実績をひもづけ、根拠（引用元・ページ数・URL）を明示したBtoG提案書（GovPro）の自動起稿。
    *   Word/Excel申請書原本へのテンプレート自動マッピング。
5.  **市民合意・投票 (`plurality-connect`)**
    *   地域や団体内での合意形成を図るスレッド形式の協議スペース。
    *   二次投票（Quadratic Voting）等のデジタル投票を用いた合意統計・意見クラスタリング。
6.  **クラウドファンディング・ボランティア実行 (`volunteer-connect`)**
    *   NPOによるクラウドファンディングキャンペーンの作成とStripe決済連携（冪等性チェック対応）。
    *   ボランティア・プロボノ案件の登録、スキルベースでのセマンティックマッチング。
    *   活動完了時の自己主権型デジタル実績証明（DID & zk-SNARKs/Groth16 Verifiable Credentials）の発行。

## 🎨 デザインシステム : Cosmic Glass (コズミック・グラス)

UI/UXは、アクリル調（グラスモフィズム）を基調とした洗練されたモダンなデザインを採用しています。
*   **カラーテーマ**: ダークモード（インディゴ、ディープネイビー）と鮮やかなネオンミント/アクアブルーのアセントカラー。
*   **レイアウト**: 左固定多階層ナビゲーションサイドバー（Funds & Data / GovPro BtoG / Civic Collaboration）。

## 🛠️ システムアーキテクチャ

*   **フロントエンド**: Next.js / React (Prisma Client)
*   **バックエンド**: FastAPI / Python (クローラー、PDF/Excelパース、データ解析)
*   **データベース**: Supabase PostgreSQL (`pgvector`, `pg_trgm` 拡張)
*   **AI/GPU インフラ**: Modal (Embedding, Reranking, LLMマッチング)
*   **ブロックチェーン・暗号**: Circom / snarkjs (Groth16) / W3C Verifiable Credentials

## 📂 ディレクトリ構成

*   `docs/`
    *   [requirements.md](docs/requirements.md) : 要件定義書
    *   [specifications.md](docs/specifications.md) : 詳細仕様書（DB・API・ZKP）
    *   [detail_design.md](docs/detail_design.md) : 詳細設計書（コレクター、Sankey、マッチング、RLS）
    *   [ui_ux_design.md](docs/ui_ux_design.md) : Cosmic Glass デザインシステム仕様
    *   [architecture.md](docs/architecture.md) : システム全体構成・データフロー設計
*   `index.css` : Cosmic Glass テーマ定義・CSS変数

## 🚀 開発の準備

### 1. データベースのセットアップ (PostgreSQL / Supabase)
SQLマイグレーションファイルを適用して、テーブルとRLSポリシーを構築します。
```bash
# 拡張機能の有効化
CREATE EXTENSION IF NOT EXISTS pgvector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```
詳細は [specifications.md](docs/specifications.md) および [detail_design.md](docs/detail_design.md) を参照してください。

### 2. 環境変数の設定
プロジェクトルートに `.env` ファイルを作成し、必要なAPIキーを設定します。
```env
DATABASE_URL=postgresql://...
MODAL_ENDPOINT_URL=https://...
MODAL_API_KEY=modal-key-...
STRIPE_WEBHOOK_SECRET=whsec_...
CLAUDE_API_KEY=sk-ant-...
```
