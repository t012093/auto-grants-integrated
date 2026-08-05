# スキル仕様書: 助成金ライフサイクル管理 — 8軸採択勝率予測 (predict_win_rate.py)

> **ステータス**: 📋 設計書（実装予定）。本ドキュメントは 2026-08-05 のブラッシュアップ案。
> 現行の「採択勝率0-100%」は**「相対競争ランク」へ変更**し、絶対確率は提示しない。

---

## 1. 概要 & 目的

### 1.1 位置づけ（gate と rank の役割境界）

助成金の適合判定（`grant_eligibility_checker`）が「応募して良いか = gate」を担うのに対し、
本モジュールは「満たした上で、勝てるか = **rank**」を担う**意思決定支援レイヤー**である。

| スコア | 担当 | 用途 | 役割 |
|---|---|---|---|
| 要件充足（2層・6ゲート） | `grant_eligibility_checker`（実装済み） | 書類/資格/要件を満たすか | **gate（応募可否）** |
| 採択順位（8軸 Win-rank） | `grant_lifecycle_manager`（本仕様） | 満たした上でどれが当たりやすいか | **rank（競争順位）** |

> **二重実装しない**: 要件充足は gate、勝率（順位）は rank として併用。
> rank が絶対的な採択確率を出すことは**ない**（下記・原則1）。

### 1.2 対象エントリポイント

- `scripts/predict_win_rate.py`（📋 実装予定）— `--org-id --grant-id [--json]`
- `scripts/export_calendar_ics.py`（📋 実装予定）— iCal同期（本仕様の対象外・別節参照）

---

## 2. 設計原則（ブラッシュアップ版・最重要）

### 原則1: 絶対確率を出さない → 「競争ランク（相対順位）」

`grant_past_awards` は現状 **2件のみ**で、スコア⇄採択率の統計キャリブレーションは不能（参考 `scoring_redesign_plan.md §M`）。
にもかかわらず「0〜100%の勝率」を表示するとハルシネーションになる。

- 出力は **rank（相対順位）+ 軸別スコア** のみ。**絶対確率(%)は UI・API に一切出さない**。
- 用途は「適合(gate)を通過した助成金の中での**比較順位付け**」に限定。
- 文面は「勝率」ではなく「**競争順位 / スコアリング**」と表記する。

### 原則2: 既存アセットを再利用し「二重実装しない」

8軸のうち複数は**実装済み eligibility / Solver / past_award** の出力をそのまま流用する
（詳細は §4 の「再利用元」列）。新規計算は自走性・深刻さ・インパクト等の不足軸に限定する。

### 原則3: カバレッジ（評価軸の充足度）を併記

2層スコアラ（coverage）と同じ思想で、**「評価できた軸の割合」をランクと分離表示**する。

- 例: `競争ランク A（※評価4/8軸・暫定）` — 低データで高ランクに見せない。
- 未評価軸はオプション扱いで重み配分から除外し、`coverage` を返す。

### 原則4: 弱点 → 企画書改善の閉ループ（最大の価値）

8軸分析の「弱み」を **`grant_form_filler`（企画書生成）へフィードバック**し、次回原稿に反映する。

- 例: 「自走性 55点」→ 次回企画書の **事業継続・自主事業化節 追記**を注記
- 例: 「実現体制 65点」→ **実施体制セクションに連携先1社追加を推奨**（+15点相当）を注記
- `generate_proposal_docx.py` の「💡 自動補完注記」機構を拡張して実現（§6）。

### 原則5: 採択フィードバックで学習（キャリブレーション）

「申請 → 採択/不採択」の結果を取り込み、**軸重みを再調整**して徐々に学習させる。
現在未実装の「採択結果取り込み」と一本化（§7）。数十件蓄積するまでは rank（相対）運用。

---

## 3. 8軸定義

審査員の実採点基準に沿った8軸。各軸は**エビデンス（引用）付き**でスコア化する。

| # | 軸 | 意味 | 再利用元（実装済み） |
|:--:|---|---|---|
| 1 | 新規性・先駆性 (Uniqueness) | 既存事業との差異・先進性 | `check_eligibility.py` の `uniqueness_keywords`（新規/先進/モデル/革新 等） |
| 2 | 実現可能性・体制 (Feasibility) | 連携・体制・運営実績 | `partnership_keywords`（連携/協働/パートナー/地域住民 等）+ 実施体制 |
| 3 | 自走性・自己資金 (Sustainability) | 自己資金比率・事業継続計画 | **新規**（budget比率 + 継続記述の有無） |
| 4 | 課題の深刻さ・エビデンス (Severity) | 統計・アンケート・ニーズの具体性 | **新規**（原稿内エビデンス引用の有無） |
| 5 | 社会的インパクト・横展開 (Scalability) | 受益者数・多地域展開 | **新規**（target_audience / activity_areas 数） |
| 6 | 積算の妥当性 (Budget Precision) | 見積・不透明経費・Solver検証 | `grant_expense_validator`（Solver 検証済みフラグ） |
| 7 | 助成趣旨・テーマ適合 (Funder Intent) | 重点テーマとの意味的一致 | eligibility `sem_purpose` スコア |
| 8 | 過去の完了・信用 (Track Record) | 採択・精算完了実績 | `past_award_analyzer` + `npo_profiles.track_records` |

---

## 4. スコアリング方式

### 4.1 軸別スコアの算出

- **再利用軸（1,2,6,7,8）**: 既存モジュールの出力値を正規化（0〜1）して流用。**再計算しない**。
- **新規軸（3,4,5）**: 下記の確定的ルール + LLM定性（evidence付き）で算出。
  - 自走性: `1 - (grant.amount_max / npo.annual_budget)` を基準 + 継続記述ありで加点
  - 深刻さ: 原稿/公募要領に統計引用や当事者ニーズ記述があるか（有無で段階点）
  - インパクト: `target_audience` 数 × `activity_areas` 数 を基準に正規化
- すべて「データ欠落で評価不能」な軸は **未評価（evaluated=False）** とし、0点扱いにしない。

### 4.2 総合スコア & ランク

- 総合スコア = 評価済み軸のみの重み付き平均（**未評価軸へ0点を当てない**）
- `coverage = Σ(評価済み軸の重み)`（0〜1）
- ランク閾値（**coverage ≥ 0.5 のときのみ確定表示**、未満は `暫定` 表記）:

| ランク | 総合スコア | 表示 |
|:--:|:--:|---|
| A | ≥ 80 | `A` / coverage < 0.5 なら `A（暫定）` |
| B | 65–79 | 同 |
| C | 50–64 | 同 |
| D | < 50 | 同 |

### 4.3 弱点修正アドバイス

各軸スコアと閾値を比較し、「落とされるリスクが高い軸（下位3軸）」を抽出。
各弱点に**具体的な改善提案**（＝次の企画書に反映する注記）を生成する（§6）。

```text
【競争順位: Bランク（※評価6/8軸） スコア: 71点】
[強み] 趣旨適合 88点（重点テーマ合致） / 積算 90点（Solver検証済み）
[弱み & 改善]
 ・自走性 55点 → 「事業継続・自主事業化計画」節を企画書に追記を推奨
 ・実現体制 65点 → 実施体制に連携先1社追加（後援名義）を推奨（+15点相当）
```

---

## 5. 出力フォーマット

`predict_win_rate.py --json` の戻り値（設計）:

```json
{
  "npo_profile_id": "<uuid>",
  "grant_id": "<gid>",
  "overall_score": 71,
  "coverage": 0.75,
  "rank": "B",
  "provisional": false,
  "axes": {
    "uniqueness":   {"score": 0.6, "evaluated": true,  "source": "eligibility"},
    "feasibility":  {"score": 0.65,"evaluated": true,  "source": "eligibility"},
    "sustainability":{"score": 0.55,"evaluated": true, "source": "rule"},
    "severity":     {"score": null, "evaluated": false, "reason": "統計引用データなし"},
    "scalability":  {"score": 0.8, "evaluated": true,  "source": "rule"},
    "budget":       {"score": 0.9, "evaluated": true,  "source": "expense_solver"},
    "funder_intent":{"score": 0.88,"evaluated": true,  "source": "eligibility"},
    "track_record": {"score": 0.5, "evaluated": true,  "source": "past_award"}
  },
  "weak_axes": ["sustainability", "feasibility"],
  "improvement_notes": [
    {"axis": "sustainability", "note": "「事業継続・自主事業化計画」節を追記を推奨"},
    {"axis": "feasibility", "note": "実施体制に連携先1社追加（後援名義）を推奨"}
  ]
}
```

---

## 6. 企画書フィードバックループ（form_filler 連携）

`generate_proposal_docx.py`（実装済み）の「💡 自動補完注記」機構を拡張し、勝率の弱点を反映する。

1. `predict_win_rate` が `improvement_notes` を出力
2. `generate_proposal_docx --win-rate <notes>` が該当セクションへ**改善注記を追記**
3. 対象セクション例:
   - 自走性 → 「4. 実施体制」内 / 「事業の持続性」脚注
   - 実現体制 → 「4. 実施体制・役割分担」の連携先行
   - 深刻さ → 「1. 事業の背景・社会的課題」のエビデンス引用補強
4. すべて 🔍【勝率改善注記】として明示（人間の確認を促す）

---

## 7. キャリブレーション & 学習（採択結果フィードバック）

未実装の「採択結果取り込み」と一本化して実現する。

### 7.1 データ収集

| 項目 | 内容 |
|---|---|
| 収集タイミング | 申請提出後、審査結果通知時 |
| 記録内容 | `(org, grant, 提出日, 採択/不採択, 不採択なら理由)` |
| 入力元 | Gmail 監視（採択通知）＋ 手動（将来は自動） |

### 7.2 軸重みキャリブレーション

- **条件**: `grant_applications` が**数十件（目安50+）**蓄積されたら実施。
- **方法**: 採択/不採択ラベルに対し、8軸スコアがどう寄与したかをロジスティック回帰等で推定し、
  軸重みを更新。それまでは**相対・比較ヒューリスティック**（§2・原則1）のままで運用。
- **注意**: キャリブレーション前の総合スコアは「絶対確率」と表記しない（既存 `scoring_redesign_plan §M` と整合）。

---

## 8. DB スキーマ提案

### 8.1 `grant_win_rank`（予測結果・Upsert）

```sql
CREATE TABLE IF NOT EXISTS public.grant_win_rank (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  npo_profile_id uuid NOT NULL REFERENCES public.npo_profiles(id),
  grant_id       int  NOT NULL REFERENCES public.grants(id),
  overall_score  int,          -- 0-100
  coverage       real,         -- 評価済み軸重み (0-1)
  rank           text,         -- A/B/C/D
  provisional    boolean DEFAULT false,
  axes_json      jsonb,        -- 8軸スコア・evaluated・source
  improvement_notes jsonb,     -- 弱点改善注記
  created_at     timestamptz DEFAULT NOW(),
  updated_at     timestamptz DEFAULT NOW(),
  UNIQUE (npo_profile_id, grant_id)
);
```

### 8.2 `grant_applications`（採択フィードバック・キャリブレーション用）

```sql
CREATE TABLE IF NOT EXISTS public.grant_applications (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  npo_profile_id uuid NOT NULL REFERENCES public.npo_profiles(id),
  grant_id       int  NOT NULL REFERENCES public.grants(id),
  appeared_at    date,          -- 申請提出日
  result         text,          -- AWARDED / REJECTED / PENDING
  reject_reason  text,          -- 不採択理由（任意）
  created_at     timestamptz DEFAULT NOW()
);
```

> スキーマ適用は `supabase/migrations/` へマイグレーションを追加して行う（実装フェーズで実施）。

---

## 9. ロードマップ / フェーズ

| フェーズ | 内容 | 判定 |
|---:|---|---|
| **A** | rank 計算 + coverage 併記 + 既存アセット再利用（eligibility/Solver/past_award） | 最小実装 |
| **B** | 弱点改善注記 → `grant_form_filler` へのフィードバック閉ループ | 価値最大化 |
| **C** | `grant_applications` 収集で採択結果取り込み（Gmail監視と連接） | 学習基盤 |
| **D** | 数十件蓄積後の軸重みキャリブレーション | 精度向上 |

> Phase A/B は grant_lifecycle_manager 単体で完結。Phase C/D は「採択結果取り込み」ギャップと一体で進める。

---

## 付録: 参照ドキュメント

- マスター仕様: `docs/grant_pipeline_spec.md` §9（8軸採択予測）
- 役割境界: `docs/scoring_redesign_plan.md` §F（gate vs rank）・§M（キャリブレーション限界）
- 上流: `skills/grant_eligibility_checker/spec.md`（要件適合）
- 連携: `skills/grant_form_filler/SKILL.md`（企画書生成・自動補完注記）
- DB: `supabase/migrations/`（grants / alerts / past_awards 等）
