# task_human_ai_allocator 仕様書 (v1.0draft)

> 人間×AI タスク自動分離・ポジション設計スキルの実装仕様。
> 設計書 `SKILL.md` を実装可能な形に具体化したもの。正本は本ファイル。

---

## 1. 目的・範囲

助成金企画書（`grant_proposals` の `content_markdown` / `budget_json`）から、
事業を遂行するための**タスク群を設計し、AUTO / HYBRID / HUMAN に分類**した上で、
人間が必要な**ポジション（役割）**を定義し、DB（`proposal_project_offers`）へ書き込む。

**範囲**: タスク・ポジションの**設計とDB保存まで**。ai-note-meet への実同期は
`scripts/sync_proposal_to_ai_note_meet.py` が担当（仕様3.2の分離原則。二重実装しない）。

---

## 2. タスク設定の決定者設計（本仕様の核心）

タスクは一律に「ルール」「LLM」「人間」のどれかで決まるわけではない。
**層ごとに決定者を明確化**する。

| 層 | タスク種別 | 例 | 決定者 |
|---|---|---|---|
| **L1** | 共通枠組みタスク（全プロジェクト共通） | 行政窓口事前相談 / 企画書最終承認 / 採択通知確認 / 実績報告 | **システム（確定的テンプレート）** |
| **L2** | ポジション枠 | PM / LOCAL_DIR / SITE_OP / IT_CREATOR | **システム（事業型→ルールで選択）** |
| **L3** | 企画書固有タスク（事業内容依存） | 「子ども食堂の調理・接客」「農家2名と同意書押印」 | **ルールで捕捉（構造化セクション由来）→ 最終は人間承認** |
| **L4** | 属性タグ（AUTO/HYBRID/HUMAN） | データ収集→AUTO / 窓口相談→HUMAN | **システム（ルール）** |

### 2.1 原則
- **タスク設定の最終決定者は人間**。AI（ルール・LLM問わず）はドラフト生成であり、
  必ず人間の承認（HYBRID）を経る。誤設定の防止（偽成功しない原則に整合）。
- **ルールを主軸**とする。企画書は `generate_proposal_docx.py` の6大セクションで
  構造化されており、動的変動の大部分は事業型テンプレート＋セクション抽出で捕捉できる。
- **LLMは残余のみ（オプション）**。ルールが拾いきれない混合・未知パターンの補強。
  YAGNI 方式: 実運用で漏れが顕在化した後に追加する（初期フェーズは実装しない）。

---

## 3. 入力

| 入力 | 出所 | 用途 |
|---|---|---|
| `content_markdown` | `grant_proposals` | 全6セクションの本文。タスク分解・事業型判定の主根拠 |
| `budget_json` | `grant_proposals` | 経費配分から IT/人件費 要否を判定（IT_CREATOR等） |
| `concept_summary` / `title` | `grant_proposals` | プロジェクト名・短縮名 |
| `npo_id` / `id` | `grant_proposals` | 保存時のFK |

---

## 4. 事業型判定（ルール）

`content_markdown` の主要部をキーワードマッチして事業型を判定する。
判定不能は **`GENERAL`**（汎用型）にフォールバック（勝手に特定の型へ断定しない）。

| 事業型コード | 判定キーワード（例） |
|---|---|
| `CHILDREN_CAFETERIA` | 子ども食堂・無料食堂・食事支援・テーブル |
| `FARM_IT` | 農家・農業・営農・スマート農業・EC・出荷 |
| `COMMUNITY_EVENT` | イベント・祭・体験・ワークショップ・まつり |
| `EDU_CLASS` | 教室・講座・学習支援・AI教室・プログラミング |
| `DIGITAL_INFRA` | システム・Web・DX・デジタル基盤・クラウド |
| `GENERAL` | 上記いずれにも該当しない（フォールバック） |

> 事業型コード・キーワードは **`business_templates.json`**（config駆動）で保持し、
> コード直書きしない（プロジェクトの重みJSON方式に準拠）。

---

## 5. タスク設計（ルール）

### 5.1 L1: 共通枠組みタスク（全プロジェクト必ず）
どの事業型でも必ず必要になるタスク群を `business_templates.json` の `common_tasks` に定義。
例: 企画書最終承認 / 助成金申請書提出 / 採択通知確認 / 実績報告書作成 など。
属するポジションは原則 PM（HYBRID）。

### 5.2 L3: 事業型固有タスク（事業型テンプレート）
`business_templates.json` の `business_types.<code>.tasks` に事業型ごとのタスク群を定義。
各タスクは `{title, position, tag}` を持つ（生成時の属性初期値）。

### 5.3 構造化セクションからの変動抽出
各タスクの対象・宛先・人数・IT要否を、構造化セクションからルールで補強する。

| 変動要因 | 出所 | 捕捉方法 |
|---|---|---|
| 宛先（農家/子ども/行政） | §1 背景 / §4 実施体制 | 宛先キーワード分解 |
| 場所（現場/窓口） | §3 実施計画 | 場所キーワード |
| IT要否 | §6 経費（IT_CREATOR配分有無） | `budget_json` 判定 |
| 人数 | §4 実施体制 | 「N名」パターン抽出 |

> 抽出は**無理に決めない**。該当キーワードがない場合は、その変動タスクを生成せず
> 「要確認」ノートを付す（補完注記）。生成漏れを偽の確信で埋めない。

---

## 6. 属性タグ判定（L4: AUTO / HYBRID / HUMAN）

`SKILL.md` の判定基準（事例列挙）を**確定的キーワードルール**としてコード化する。

| タグ | 判定キーワード（例） |
|---|---|
| `AUTO` | データ収集・API・スクレイピング・PDF解析・抽出・類似度計算・初案生成・レンダリング・Wiki下書き・リマインダー送信 |
| `HUMAN` | 対面・窓口・相談・押印・署名・同意・現場運営・調理・接客・撮影・イベント参加・ファシリテーション・請求書印 |
| `HYBRID` | 最終チェック・承認・公開ボタン・確定・送信・提出・最終レビュー |

**判定規則（偽成功防止）**:
1. `HUMAN` キーワードに一致 → **HUMAN**（対面・押印は最優先）
2. それ以外で `AUTO` キーワード一致 → **AUTO**
3. それ以外で `HYBRID` キーワード一致 → **HYBRID**
4. **どのキーワードにも一致しない → HYBRID にフォールバック**
   （人間承認を挟む＝安全側。AUTO と断定しない）
5. 事業型テンプレートでタグが既に指定されている場合は**テンプレート値を優先**

> 判定キーワードは `tag_rules.json`（config駆動）で保持し、コード直書きしない。

---

## 7. ポジション設計（L2）

### 7.1 ポジションテンプレート
`business_templates.json` の `positions` に、事業型 × ポジションセットを定義。

| position_code | ポジション名 | デフォルト capacity | 主な責任 |
|---|---|---|---|
| `PM` | プロジェクトリーダー | 1 | 全体進行・行政事前相談・予算管理・最終承認 |
| `LOCAL_DIR` | 地域・パートナー連携ディレクター | 1 | 現地NPO・農家との面談・同意書獲得 |
| `SITE_OP` | 現場運営・イベント担当 | 1〜3 | 子ども食堂・教室の会場準備・当日運営 |
| `IT_CREATOR` | IT/EC・広報クリエイター | 1〜2 | Webサイト構築・デザイン制作の最終チェック |

- 事業型に応じ、不要なポジションは省略、兼務可能なら統合。
- 各ポジションの `task_allocation_tag` は、担当タスク群の主要タグ
  （例: SITE_OP→HUMAN / IT_CREATOR→HYBRID / LOCAL_DIR→HUMAN / PM→HYBRID）。

### 7.2 人件費配分ルール
- 総予算の **30%〜40%** を人件費・作業手当として計上可能（`budget_json` の合計から算出）。
- 各ポジションの `compensation_notes` は、想定工数（時間 × 時給）に基づく概算を注記として生成
  （例: 「時給2,000円 × 想定200h = 40万円」）。確定額は人間が調整（HYBRID）。

---

## 8. 出力（DB書き込み・現行スキーマを使用）

**DBスキーマは変更しない**。既存 `proposal_project_offers`（および関連）へ書き込む。

### 8.1 保存先テーブル
| テーブル | 保存内容 |
|---|---|
| `proposal_project_offers` | ポジション1件ずつ（`position_code` / `position_name` / `capacity` / `task_allocation_tag` / `compensation_notes` / `initial_tasks_json` / `status=RECRUITING`） |

- **タスクはポジション単位で `initial_tasks_json` に格納**（タスク単位の新テーブルは追加しない＝YAGNI）。
- 各タスクのタグは、`initial_tasks_json` の各要素に `{title, tag}` として保持。

### 8.2 冪等性
- 同一 `proposal_id` に対する再実行は、既存 `proposal_project_offers` を**一旦削除して再作成**（DESELECT & INSERT）か、
  `position_code` で Upsert する。方針は `--reset` フラグで選択可。
- デフォルトは **Upsert（position_code 単位で上書き）**。

---

## 9. 出力 JSON（CLI・機械可読）

CLI では設計結果を以下の JSON として出力（`--json`）。

```json
{
  "proposal_id": "<UUID>",
  "project_name": "【助成金名】プロジェクト短縮名",
  "business_type": "FARM_IT",
  "tasks": [
    {"title": "jGrants API から公募要項PDFを取得・解析", "tag": "AUTO", "assigned_position": null},
    {"title": "企画書本文の最終チェック・承認", "tag": "HYBRID", "assigned_position": "PM"},
    {"title": "農家2名との対面協議・同意書受領", "tag": "HUMAN", "assigned_position": "LOCAL_DIR"}
  ],
  "positions": [
    {"position_code": "PM", "position_name": "プロジェクトリーダー", "capacity": 1,
     "permissions": "ADMIN", "compensation_notes": "時給2,000円 × 200h = 40万円",
     "initial_tasks": ["行政窓口との事前相談", "500万円予算執行の進捗管理"]}
  ],
  "offer_announcement_text": "【新プロジェクト発足】...(募集文面)"
}
```

---

## 10. ai-note-meet 連携（委譲）

- `sync_proposal_to_ai_note_meet.py` が `proposal_project_offers` の
  `position_name` / `capacity` / `task_allocation_tag` / `initial_tasks_json` を読み、
  `create_announcement` / `create_task` / `create_calendar_entry` の MCP 計画を生成（実呼び出しは Agent）。
- task_human_ai_allocator はここまで関与しない（**二重実装しない**）。
- 連携の詳細は `docs/grant_proposal_collaboration_spec.md` §3.2 に従う。

---

## 11. 検証

| 項目 | 内容 |
|---|---|
| 単体テスト | 事業型判定（キーワード→型）/ タグ判定（HUMAN優先・フォールバック）/ ポジション設計 / JSON出力形状 / DB書き込み（モック） |
| 境界 | `npx @naoya.k/spaghetti-guard check` |
| フルスイート | `env -u PYTHONPATH uv run pytest` |

テスト観点（偽成功防止）:
- 判定不能な事業型 → `GENERAL` にフォールバック
- タグ不明なタスク → `HYBRID` にフォールバック（AUTOと断定しない）
- キーワード未検出の変動 → タスク生成せず「要確認」ノート

---

## 12. ロードマップ

| Phase | 内容 | 決定者 |
|---|---|---|
| **Phase 1（本実装）** | L1+L2+L4（共通タスク・ポジション・属性タグ）+ L3のルール捕捉。確定的ルールのみ | システム＋人間最終承認 |
| **Phase 2（オプション）** | L3残余の LLM 補強（混合・未知パターン）。漏れが顕在化してから追加（YAGNI） | AI提案→人間承認 |

---

## 13. 実装ファイル（予定）

| ファイル | 内容 |
|---|---|
| `skills/task_human_ai_allocator/scripts/task_human_ai_allocator.py` | 本体（解析→タグ→ポジション→DB書き込み） |
| `skills/task_human_ai_allocator/business_templates.json` | 事業型×タスク/ポジション テンプレート（config駆動） |
| `skills/task_human_ai_allocator/tag_rules.json` | AUTO/HYBRID/HUMAN 判定キーワード（config駆動） |
| `tests/test_task_human_ai_allocator.py` | 単体テスト |

---
**正本**: 本ファイル（`spec.md`）。設計の大枠は `SKILL.md`（分類基準の由来）。
実装フェーズでは本 spec に従い、変更時は本 spec も同時に更新する。
