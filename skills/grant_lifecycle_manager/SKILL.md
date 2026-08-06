---
name: grant_lifecycle_manager
description: 実効的8軸モデルによる採択予測 (相対競争ランク A/B/C/D・絶対確率%は出さない)、公募締切・準備タスクの iCal (.ics) カレンダー同期、およびDB差分巡回による新着助成金・締切接近アラートを管理するライフサイクル統合スキル。
---

# 助成金ライフサイクル管理スキル (grant_lifecycle_manager)

## 概要

助成金の発見から申請・採択までのライフサイクル全体を統合管理します。実効的 8 軸の採択勝率予測、公募締切・書類準備タスクのカレンダー同期、および新着・変更助成金の自動アラート通知を提供します。

---

## 付属スクリプト

### `scripts/predict_win_rate.py` 📋 実装予定
実効的 8 軸モデルによる採択勝率を予測する CLI スクリプト。

#### 実行方法

```bash
# 採択勝率を予測
uv run skills/grant_lifecycle_manager/scripts/predict_win_rate.py \
  --org-id "org-uuid-1234" --grant-id "g-456"

# JSON 形式で結果出力
uv run skills/grant_lifecycle_manager/scripts/predict_win_rate.py \
  --org-id "org-uuid-1234" --grant-id "g-456" --json
```

### `scripts/export_calendar_ics.py` 📋 実装予定
公募締切および準備タスクを iCal (`.ics`) 形式でエクスポートする CLI スクリプト。

#### 実行方法

```bash
# 特定助成金の締切・タスクを iCal 出力
uv run skills/grant_lifecycle_manager/scripts/export_calendar_ics.py \
  --grant-id "g-456"

# 全 OPEN 助成金の締切をまとめて出力
uv run skills/grant_lifecycle_manager/scripts/export_calendar_ics.py --all-open

# 出力先を指定
uv run skills/grant_lifecycle_manager/scripts/export_calendar_ics.py \
  --grant-id "g-456" --output "deadlines.ics"
```

---

## パラメーター仕様一覧

### predict_win_rate.py

| パラメーター | 型 | 説明 | 使用例 |
|---|---|---|---|
| `--org-id` | `string` | 登録団体 UUID | `--org-id "org-uuid-1234"` |
| `--grant-id` | `string` | 対象助成金 ID | `--grant-id "g-456"` |
| `--json` | `flag` | JSON 形式で結果出力 | `--json` |

### export_calendar_ics.py

| パラメーター | 型 | 説明 | 使用例 |
|---|---|---|---|
| `--grant-id` | `string` | 対象助成金 ID | `--grant-id "g-456"` |
| `--all-open` | `flag` | 全 OPEN 助成金の締切をまとめて出力 | `--all-open` |
| `--output` | `string` | 出力ファイルパス（デフォルト: `grant_deadlines.ics`） | `--output "deadlines.ics"` |

---

## 実効的 8 軸採択予測モデル (SOP)

> **📋 設計正本**: [spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/skills/grant_lifecycle_manager/spec.md)（ブラッシュアップ版）
> **重要**: 本モジュールは「採択勝率（絶対確率）」ではなく**「相対競争順位（rank）+ 軸別スコア + coverage（評価充足度）」**を出力する。
> 採択データが少ない現状で絶対確率を出さない（`scoring_redesign_plan §M`・spec §2-原則1）。

### 8 大評価軸

| # | 評価軸 | 判定に使用するデータ |
|:---:|---|---|
| 1 | **新規性・先駆性** (Uniqueness) | 団体活動 vs 既存事業の差異、先進的取り組みの有無 |
| 2 | **事業実現可能性・体制** (Feasibility) | 連携協定・後援の有無、スタッフ資格・運営実績 |
| 3 | **自走性・自己資金確保** (Sustainability) | 自己資金比率 (%)、助成終了後の事業継続計画 |
| 4 | **課題の深刻さ・エビデンス** (Severity) | 地域統計データ、当事者アンケート、ニーズの具体性 |
| 5 | **社会的インパクト・横展開** (Scalability) | 推定受益者数、他地域展開可能性 |
| 6 | **積算の妥当性・費用根拠** (Budget Precision) | 見積書添付率、不透明経費比率、Solver 検証済みか |
| 7 | **助成趣旨・テーマ適合** (Funder Intent) | 財団の重点公募テーマとのセマンティックマッチ度 |
| 8 | **過去の完了・信用実績** (Track Record) | 過去の助成金採択・精算完了フラグ |

### 出力フォーマット

```text
【実効採択予測スコア: 82点 / 100点 - Aランク】

[強み]
 課題のエビデンス (90点): 地域統計データが引用されており、ニーズが明確。
 助成趣旨適合 (88点): 今期の重点テーマに合致。

[弱み & 改善提案]
 ⚠️ 自走性 (55点): 助成金依存度が高い。自主事業化計画の追記を推奨。
 ⚠️ 実現体制 (65点): 連携先の記載なし。後援名義1社以上の追加で+15点向上見込み。
```

---

## カレンダー & タスク同期 (SOP)

### 自動生成されるカレンダーイベント

| イベント | タイミング | 内容 |
|---|---|---|
| 📅 **公募締切** | 締切日当日 | 「○○助成金 公募締切」 |
| ⏰ **書類準備リマインド** | 締切 14 日前 | 「定款・決算書の準備確認」 |
| ⏰ **最終提出リマインド** | 締切 7 日前 | 「申請書最終確認 & 提出」 |
| 📋 **書類取得期限** | 締切 21 日前 | 「納税証明書の取得（発行に7日要）」 |

---

## 関連ドキュメント

* 📋 **仕様正本（ブラッシュアップ版）**: [spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/skills/grant_lifecycle_manager/spec.md)（rank・coverage・既存再利用・企画書フィードバック・学習）
* 📘 [grant_pipeline_spec.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/grant_pipeline_spec.md) (パイプライン統合仕様 §9, §10)
* 📘 [scoring_redesign_plan.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/scoring_redesign_plan.md) (§F: gate/rank 役割境界, §M: キャリブレーション限界)
* ✅ [grant_eligibility_checker SKILL.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/skills/grant_eligibility_checker/SKILL.md) (17項目適合判定の上流)
* 🔄 [grant_form_filler SKILL.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/skills/grant_form_filler/SKILL.md) (弱点改善注記のフィードバック先)
* 🗄️ [specifications.md](file:///Users/2005nk/Works/npo/civic/auto-grants-integrated/docs/specifications.md) (`public.alerts` DDL)
