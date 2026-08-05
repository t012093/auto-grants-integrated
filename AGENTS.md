# AGENTS.md - Agentic Development Guidelines (`auto-grants-integrated`)

> [!IMPORTANT]
> このファイルは、AIエージェント（Antigravity, Cursor, Copilot 等）が本プロジェクト（**Civic Grant Intelligence Platform: `auto-grants-integrated`**）で開発を行う際の最優先行動規範および仕様のガイドラインです。

---

## 1. プロジェクト概要 & 全体像

本システムは、NPO・市民活動団体と自治体・財政データを結びつけ、助成金自動選定から申請書生成（`GovPro`）、予算フロー可視化（Sankey/`zaisei-radar`）、市民合意（二次投票/`plurality-connect`）、資金調達・DID/ZKP実績証明までをワンストップで支援する統合プラットフォームです。

### 構成モジュール & ディレクトリ構造
* **`backend/`**: FastAPI 統合バックエンド (`collectors`, `extract`, `normalize`)
* **`supabase/migrations/`**: PostgreSQL / Supabase スキーマ (`pgvector`, `pg_trgm`, RLS)
* **`skills/`**: 自律型タスクスキル群（適合判定 `grant_eligibility_checker`, 経費チェック, 申請書自動入力 `grant_form_filler` 等）
* **`scripts/`**: DBマイグレーション適用・データ流入・検証用自動化スクリプト
* **`docs/`**: プロジェクト仕様書正本マスター

---

## 2. 最優先行動原則 (Global Rules)

1. **推測の絶対禁止 (No Guessing)**: 
   * ライブラリ仕様、型、既存コードの挙動について不確実な場合は絶対に推測でコードを書かないこと。コードベース内を検索するか `docs/` を確認すること。
2. **シェル経由コード直渡しの厳禁**: 
   * バッククォート（```）、`$()` 等を含むソースコードや Markdown をワンライナーやヒアドキュメント（`python3 -c "..."` や `cat << 'EOF'`）でシェルコマンドに直接渡すことを厳禁とする。必ず専用のファイル書き込みツールを使用すること。
3. **自己修正ループの制限 (最大3回)**: 
   * 自動テストや Lint が失敗した際、自動修正試行は**最大3回まで**とする。3回で解決しない場合は根本原因の仮説をユーザーに報告し、指示を仰ぐこと。
4. **日本語応答・簡潔性**: 
   * チャット応答・報告はすべて**日本語**で行い、挨拶や不要な解説は省略すること。

---

## 3. ドキュメント & 仕様の正本 (Single Source of Truth)

本プロジェクトにおける仕様の正本は以下のように厳格に管理されます。コード変更時は対応するドキュメントも**必ず同時に更新**してください。

| レイヤー | 正本ファイル | 役割・定義内容 |
| :--- | :--- | :--- |
| **全体仕様書** | [`docs/specifications.md`](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/specifications.md) | DB設計、RPC/API定義、ZKP/DID仕様、Embedding仕様 (v2.0 Draft) |
| **アーキテクチャ** | [`docs/architecture.md`](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/architecture.md) | システム全構成・パイプライン・Sankey可視化・Modal GPU/GraphRAG |
| **API契約書** | [`docs/api_contract.md`](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/api_contract.md) | FastAPI/MCPエンドポイント・Pydantic v2 スキーマ定義 |
| **要件定義** | [`docs/requirements.md`](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/requirements.md) | 機能・非機能要件 |
| **詳細設計** | [`docs/detail_design.md`](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/detail_design.md) | コレクター、Sankey、マッチング、RLSポリシー |
| **タスク・負債** | [`docs/TODO.md`](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/TODO.md) | タスク追跡・技術的負債トラッカー |

---

## 4. プロジェクト固有の技術規約 & ガードレール

### Python & パッケージ管理 (`uv` 必須)
* **`pip` や `poetry` の使用は厳禁**。パッケージ追加、仮想環境実行、スクリプト実行は必ず **`uv`** を使用すること。
  * 依存追加: `uv add <package>`
  * スクリプト実行: `uv run python <script.py>`
  * テスト実行: `uv run pytest`

### AI / ベクトル検索 / Embedding 仕様
* **モデル制限**: ベクトル検索には **`BAAI/bge-m3` (1024次元)** を統一使用すること（ONNX / ONNX-Runtime）。
* **動作検証**: `.spaghetti-guard/verify_model.py` でベクトル次元数（1024次元）の整合性がテストされているため、独自にモデル ID を勝手に変更しないこと。

### FastAPI & Pydantic v2 開発規約
* Pydantic は **v2** を使用。
* スキーマ定義で Forward Reference（前方参照）を使用している場合は、必ずファイル末尾で `ModelName.model_rebuild()` を明示的に呼出すること（[`docs/api_contract.md`](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/api_contract.md) 準拠）。

### アーキテクチャ境界検証 (Spaghetti Guard)
* コード変更後は `npx @naoya.k/spaghetti-guard check` で静的境界ルールをセルフチェックすること。
* 複雑なコアファイルをリファクタリングする際は、事前に対象ファイルを凍結するコマンド（`npx @naoya.k/spaghetti-guard freeze <filepath>`）を実行して挙動固定用テスト（Characterization Test）を生成すること。

---

## 5. 推奨ワークフロー (Workflow)

1. **調査 (Explore)**: 関連コードを検索し、[`docs/specifications.md`](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/specifications.md) や [`docs/api_contract.md`](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/api_contract.md) の仕様を確認。
2. **計画 (Plan)**: 大きな変更を行う場合は、`implementation_plan.md` を作成して変更ファイルとアプローチを人間に提示し、合意を得る。
3. **実行 (Execute)**: 最小限かつ安全な編集を実施。
4. **検証 (Verify)**: 自動検証コマンドを実行してエラーがないことを確認する。

---

## 6. 自動検証コマンド (Verification Commands)

> [!IMPORTANT] 実行環境 (verified 2026-08)
> 親シェルに `PYTHONPATH`（例: `~/.hermes/hermes-agent/venv/lib/python3.11/site-packages`）が
> 設定されていると、`.venv`（Python 3.14）に `uv run` しても numpy 等が 3.11 用 `.so` を
> 読み `No module named 'numpy._core._multiarray_umath'` で全テスト失敗する。
> **必ず `env -u PYTHONPATH` を付けて実行すること。**

作業完了前に、必ず以下の検証コマンドを実行して全PASSを確認すること。

```bash
# 1. Python ユニットテスト・統合テスト実行
env -u PYTHONPATH uv run pytest

# 2. Spaghetti Guard アーキテクチャ境界チェック
npx @naoya.k/spaghetti-guard check

# 3. モデル次元数動作チェック (Embedding検証)
env -u PYTHONPATH uv run python .spaghetti-guard/verify_model.py
```
