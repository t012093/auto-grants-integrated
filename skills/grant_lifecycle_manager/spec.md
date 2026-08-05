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

### 1.1.1 過去採択データの定義（重要・認識の明確化）

本スキルの「過去採択データ」とは、**対象助成金・財団が過去にどれを採択してきたか**の
実績（採択団体名・事業名・採択額・選定評コメント等）を、実Webサイトから
**リサーチして収集したデータ**（`public.grant_past_awards`）を指す（`past_award_analyzer` が担当）。

- ❌ ではない: 自社の応募実績・精算実績（それは `grant_applications`／`npo_profiles.track_records`）。
- ✅ だ: 「この財団は過去、どんなテーマ・規模・体制の事業を採択してきたか」＝**助成金側の採択傾向・勝因パターン**。
- このデータを**8軸の根拠（ベンチマーク）**とし、自団体を当てはめて勝因フィット度を測る（§4 参照）。
- 収集・分析は `past_award_analyzer`（`crawl_past_awards` → `analyze_past_awards`）の**5大視点**に委譲する。

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
| 1 | 新規性・先駆性 (Uniqueness) | 既存事業との差異・先進性 | **新規**（企画/活動の新規・先進記述の有無で段階点） |
| 2 | 実現可能性・体制 (Feasibility) | 連携・体制・運営実績 | **新規**（連携/後援/体制記載の有無・数で段階点） |
| 3 | 自走性・自己資金 (Sustainability) | 自己資金比率・事業継続計画 | **新規**（budget比率 + 継続記述の有無） |
| 4 | 課題の深刻さ・エビデンス (Severity) | 統計・アンケート・ニーズの具体性 | **新規**（原稿内エビデンス引用の有無） |
| 5 | 社会的インパクト・横展開 (Scalability) | 受益者数・多地域展開 | **新規**（target_audience / activity_areas 数） |
| 6 | 積算の妥当性 (Budget Precision) | 見積・不透明経費・Solver検証 | `grant_expense_validator`（Solver 検証済みフラグ） |
| 7 | 助成趣旨・テーマ適合 (Funder Intent) | 重点テーマとの意味的一致 | eligibility `sem_purpose` + **過去採択ベンチマーク** |
| 8 | 過去の完了・信用 (Track Record) | 採択・精算完了実績 | `npo_profiles.track_records`（自社信用） |

> 注: 軸7・8は「自社の信用実績（track_records）」と「助成金側の過去採択傾向（grant_past_awards）」を**別物として区別**する。前者は自社の過去実績、後者は**8軸全体の根拠ベンチマーク**（§3.1）として使う。

### 3.1 過去採択データ（勝因パターン）→ 8軸への反映

`past_award_analyzer` がリサーチ・分析する**5大視点**を、8軸の採点根拠として利用する：

| past_award_analyzer 5大視点 | 主に反映する8軸 | 反映方法 |
|---|---|---|
| 課題設定の切り口 (Problem Framing) | 新規性 / 深刻さ | 採択事業の課題提示パターンとの一致度 |
| 解決アプローチ・体制 (Solution Model) | 実現可能性 | 単独/連携体制の傾向と自社体制の一致 |
| 金額・予算の相場感 (Budget Range) | 積算の妥当性 | 採択額相場・満額率に自社予算を合わせる |
| 定量成果・KPI 指標 (Impact Metrics) | インパクト / 積算 | 採択者が出す KPI 水準をベンチマーク |
| 審査評・選定理由 (Evaluator Feedback) | 趣旨適合 / 自走性 | 「連携」「継続性」「波及効果」等のキーワード一致 |

> つまり**8軸は自社のプロフィールを「過去採択データ（勝因パターン）」に当てはめた一致度**で採点する。
> 過去採択データの有無・件数は `coverage`（§4.2）に反映し、データが乏しい軸は `evaluated=False` にする。
> 収集実行は `crawl_past_awards.py --grant-id ... --profile ... --save-db` を事前に呼ぶ前提（`analyze_past_awards` へ委譲）。

---

## 4. スコアリング方式

### 4.1 重み定義と更新方針（3段階）

> **現在は専門家初期値（`v1 expert prior`）を使用**。データ蓄積に応じて Stage 2 → 3 で更新する。
> 重みは**コード直書きせず config（JSON）で管理**し、差し替え可能にする（eligibility の `axes_config` と同方式）。

**8軸の重み（v1 expert prior・合計1.00）**

| # | 軸 | 重み | 根拠 |
|:--:|---|---:|:---|
| 7 | 助成趣旨・テーマ適合 (Funder Intent) | **0.20** | 財団の重点テーマと外れれば他が無意味。`sem_purpose` + 過去採択基準で計測可 |
| 4 | 課題の深刻さ・エビデンス (Severity) | **0.15** | 課題をどの根拠で提示するかを見る |
| 2 | 実現可能性・体制 (Feasibility) | **0.15** | 連携・体制（行政/企業/他NPO）が整っているか |
| 1 | 新規性・先駆性 (Uniqueness) | **0.10** | 差別化ポイント |
| 5 | 社会的インパクト・横展開 (Scalability) | **0.10** | 受益者規模・波及 |
| 6 | 積算の妥当性 (Budget Precision) | **0.10** | 見積・不透明経費。`expense_solver` 検証済みで計測可 |
| 3 | 自走性・自己資金 (Sustainability) | **0.10** | 助成終了後の継続性評価の増加に対応 |
| 8 | 自社の過去信用実績 (Track Record) | **0.10** | Track Record |

**更新方針（Stage）**

| Stage | トリガー | 重みの出所 |
|---|---|---|
| **1**（現在） | 過去採択データが乏しい | 上記の `v1 expert prior`（ドメイン工学的初期値と明記） |
| **2** | 過去採択データ 数十件 | `past_award_analyzer` 5大視点の「審査評・選定理由」頻出語（連携/継続性/波及等）から**強調軸を推定**して重み更新 |
| **3** | 自社応募結果 50+件 | 採択/不採択ラベルに対する各軸の寄与をロジスティック回帰等で推定し再配分 |

> rank は相対比較なので、重みの絶対値より **一貫性 + coverage** が正しさを担保する（絶対確率と表記しない）。

### 4.2 軸別スコアの算出

- **再利用軸（6,7）**: eligibility_v2 report の `axes` 出力（`budget` / `expense` / `sem_purpose` / `req_rag` 等）を正規化（0〜1）して流用。**再計算しない**。
- **新規軸（1,2,3,4,5,8）**: 下記の確定的ルール + LLM定性（evidence付き）で算出。
  - 自走性: `1 - (grant.amount_max / npo.annual_budget)` を基準 + 継続記述ありで加点
  - 深刻さ: 原稿/公募要領に統計引用や当事者ニーズ記述があるか（有無で段階点）
  - インパクト: `target_audience` 数 × `activity_areas` 数 を基準に正規化
  - 新規性: 企画書/活動に「新規/先進/モデル/革新」等の要素記述があるか（段階点）
  - 実現可能性: 連携・後援・体制記載の有無・数（段階点）
  - 自社信用: `npo_profiles.track_records` の採択・精算完了実績数
- すべて「データ欠落で評価不能」な軸は **未評価（evaluated=False）** とし、0点扱いにしない。
- **LLM定性は eligibility と同じ規約**（evidence引用必須・数値創作禁止・評価不能なら evaluated=False）。

### 4.3 総合スコア & ランク

- 総合スコア = 評価済み軸のみの重み付き平均（**未評価軸へ0点を当てない**）
- `coverage = Σ(評価済み軸の重み)`（0〜1）
- ランク閾値（**coverage ≥ 0.5 のときのみ確定表示**、未満は `暫定` 表記）:

| ランク | 総合スコア | 表示 |
|:--:|:--:|---|
| A | ≥ 80 | `A` / coverage < 0.5 なら `A（暫定）` |
| B | 65–79 | 同 |
| C | 50–64 | 同 |
| D | < 50 | 同 |

> 過去採択データが**完全欠如（0件）**の助成金（初年度公募等）は rank の意味が無いため、**rank を返さず `insufficient_data` 応答**とする。

### 4.4 弱点修正アドバイス

各軸スコアと閾値を比較し、「落とされるリスクが高い軸（下位3軸）」を抽出。
各弱点に**具体的な改善提案**（＝次の企画書に反映する注記）を生成する（§6）。
> 改善注記の具体値（「+15点」等）は**過去採択データの実数値のみ**を参照し、数値を創作しない。

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

## 7. キャリブレーション & 学習

> **過去採択リサーチ（grant_past_awards）が予測の主根拠**であり、下記の自社応募結果（grant_applications）は
> 「自社向けの精度検証・追加キャリブレーション」として扱う（主根拠の代替ではない）。

### 7.1 予測の主根拠：過去採択リサーチ

- 対象助成金の**複数年度分の採択実績**を `crawl_past_awards.py` でリサーチ・収集し、5大視点の勝因パターンを作る（§3.1）。
- 件数が少ない（参考: 現状2件）間に無理に傾向を出さず、`coverage` を下げて**暫定**扱いにする（§4.2）。
- `scoring_redesign_plan §M` の「キャリブレーションは採択データ蓄積を待つ」は、この**助成金側の過去採択データ量**を指す。

### 7.2 自社応募結果（grant_applications）による精度検証・追加学習

| 項目 | 内容 |
|---|---|
| 収集タイミング | 自社の申請提出後、審査結果通知時 |
| 記録内容 | `(org, grant, 提出日, 採択/不採択, 不採択なら理由)` |
| 入力元 | Gmail 監視（採択通知）＋ 手動（将来は自動） |

- 目的: 8軸スコアが「自社の実際の採択/不採択」と整合するかを**検証**し、軸重みを微調整（追加学習）。
- **条件**: `grant_applications` が数十件（目安50+）蓄積されたら実施。
- **注意**: キャリブレーション前の総合スコアは「絶対確率」と表記しない（§2・原則1 と整合）。

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
