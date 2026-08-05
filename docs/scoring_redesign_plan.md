# スコアリング再設計 実装計画書 (scoring_redesign_plan.md)

> 対象: `auto-grants-integrated` / 助成金要件スコアリング (check_eligibility)
> ステータス: **DRAFT — レビュー・合意待ち**
> 関連: `grant_pipeline_spec.md` §6, `grant_eligibility_checker/spec.md`, `check_eligibility.py` (6-Gate)

---

## 1. 背景・現状の問題

現行 `check_eligibility.py` は 6-Gate で、最終スコア = `G6(書類準備率)×0.4 + G4(セマンティック)×0.6`。

実データ確認の結果、以下の問題により**「要件充足スコア」として機能していない**:

| # | 問題 | 実態 |
|---|---|---|
| 1 | **書類チェック空振り** | `grants.required_documents` が全件空 → G6 は一律 100 点（評価していないのに満点扱い） |
| 2 | **RAG がデータ不足で誤った自信** | `knowledge_chunks` は grant 2,3 のみ → 残りは G4 のフォールバック(70〜85点)で埋める |
| 3 | **G5(RAG) が総合スコアに非反映** | 要件充足度がスコアに加算されない（FAIL時のみ弾く） |
| 4 | **重みが 2 軸のみ** | `0.4/0.6`。経費・書類数・定性などを反映しない |
| 5 | **LLM 定性合致が削除された** | 旧 Stage3 にあった「公募趣旨 vs 団体ミッション」の品質評価が無くなった |
| 6 | **データ欠落を考慮しない** | 深掘り前の「浅い一次判定」なのに、信頼度の低い値を確定スコアとして出す |
| 7 | **文書(仕様)が旧ロジックのまま** | spec.md / SKILL.md は旧 3段階・17項目。コード(6-Gate)と不一致 |

**根因**: 「浅い一次判定で篩 → 深掘り(書類DL/RAG投入) → 本スコア」の**段階化が実装されておらず**、常に浅いデータだけで確定スコアを吐いている。

---

## 2. 目標スコアリング仕様（2層モデル）

### 層1: ハードエリジビリティゲート（0/1・スコア外）

**1つでも不合格 → `INELIGIBLE`**（理由付き。スコアは出さない）。

| Gate | 判定 | データ源 |
|---|---|---|
| E1 対象法人格 | `npo.organization_type ∈ grants.eligible_org_types` | npo_profiles / grants |
| E2 対象地域 | `grants.target_area` ⊇ 団体拠点/活動地域（全国=常に可） | npo(拠点/活動地域) / grants |
| E3 実績年数 | `years_active ≥ grants.min_years_active` | npo_profiles / grants |
| E4 公募状態 | `status=OPEN` かつ 締切 ≥ 今日 | grants |

※不適合でも「あと一歩」を検知したい場合のみ、E3 は境界値(ギリギリ)をフラグ化して層2に渡す（既定OFF）。

### 層2: 要件充足スコア（evidence-weighted・0-100）

各軸 `i` に **(重み w_i, サブスコア s_i∈[0,1], 評価済フラグ e_i)** を持たせる。

| 軸コード | 名称 | データ源 | 評価ロジック | 重み w_i |
|---|---|---|---|---|
| `budget` | 予算規模適合 | grants.amount_max vs npo.annual_budget | 機械（適正比。例: 上限が年予算の15〜60%を最良とし、超過/過小を減点） | 0.10 |
| `sem_activity` | 活動分野セマンティック | knowledge_chunks vs npo_knowledge_chunks(ACTIVITY_TAGS) | RAG (pgvector コサイン) | 0.15 |
| `sem_audience` | 対象層セマンティック | …(TARGET_AUDIENCE) | RAG (pgvector) | 0.10 |
| `sem_purpose` | 事業目的一致性 | grants.detail_text vs npo概要 | RAG (pgvector) | 0.10 |
| `req_rag` | 特定要件RAG充足 | requirement_sentences → npo_knowledge_chunks | RAG（要件ごと 類似度≥0.70 の合格率） | 0.20 |
| `docs` | 書類準備率 | grants.required_documents vs npo.prepared_documents | 機械（集合差分: 準備済/必須） | 0.15 |
| `expense` | 経費ルール整合 | grant_expense_rules vs npo_expense_preferences | 機械（Solver 整合率 = 配分可能額/希望額） | 0.10 |
| `qual` | 定性ミッション合致 | grants.detail_text(公募趣旨) vs npo.description | LLM（原文引用必須）※既定OFF・フラグで有効化 | 0.10 |

**重み合計 = 1.00**（既定値。外部JSONで調整可能）。

### 評価カバレッジ（旧問題 #2/#4/#6 の解消）

```
W         = Σ w_i · e_i                 (評価済み重み)
coverage  = W × 100 (%)                 (重み基準の評価カバレッジ)
TotalScore= (Σ w_i · s_i · e_i) / W     (W>0 のとき。e_i=0 の軸は無視＝実際に入れない)
```

- **データが無い軸は無理に補完しない。** 重みを評価済み軸で再正規化し、`coverage` を明示。
- 深掘り前（書類/経費/要件RAGが空）で `coverage が低い` → **「PROVISIONAL（一次篩・要深掘り）」** として確定判定を保留。※従来の G4 フォールバック(75点)や G6 一律100は**廃止**。

### 判定しきい値

| 状態 | 条件 |
|---|---|
| `INELIGIBLE` | 層1 のハードゲート失敗 |
| `PROVISIONAL` | 層1 通過 かつ coverage < 60%（要深掘り） |
| `ELIGIBLE` | coverage ≥ 60% かつ TotalScore ≥ 75 |
| `CONDITIONAL` | coverage ≥ 60% かつ 55 ≤ TotalScore < 75 |
| `INELIGIBLE` | coverage ≥ 60% かつ TotalScore < 55 |

※PROVISIONAL は await 深掘り。実データ投入後に自動で ELIGIBLE/CONDITIONAL/INELIGIBLE へ確定。

---

## 3. 深掘りとの連携（Phase A→B→C の閉ループ）

`check_eligibility` は出力に **未評価軸（e_i=0）リスト** を含める。
深掘りプランナーはこれを読んで Phase B を発火する:

1. **PDF URL 収集**: `grants.detail_text`（HTML）内の `<a href>` から公募要領 PDF を抽出 → `attachment_urls` 投入。（現状 attachment_urls は空。ここが不足ステップ）
2. **extract_pdf.py 実行**: PDF→テキスト→`required_documents` / `grant_expense_rules` / `knowledge_chunks` / **`requirement_sentences`** を一括投入＋ベクトル化。
   ※現行 extract_pdf は `requirement_sentences` を書かない → **追加実装が必要**（§5.3）。
3. **再スコア**: データ投入後、層2の該当軸で coverage が上がり、`PROVISIONAL` → 確定へ。

---

## 4. アラート & 出力

`public.alerts.report_json` に以下を保存（既存構造を拡張）:
```jsonc
{
  "hard_gates": { "E1": "PASS", "E2": "FAIL", ... },
  "axes": { "budget": {"score": 0.8, "weight": 0.10, "evaluated": true, "evidence": "..."}, ... },
  "total_score": 82,
  "coverage": 0.75,
  "overall_status": "ELIGIBLE",
  "unevaluated_axes": ["req_rag", "expense", "qual"],
  "deep_dive_action": "harvest_pdf:grant=20"
}
```

- alert メッセージには `スコア(カバレッジ)` を表示。coverage 低いものは「要深掘り」と明示。

---

## 5. 実装ファイル変更一覧

### 5.1 `skills/grant_eligibility_checker/scripts/check_eligibility.py`（要改修）
- 6-Gate を **層1(ハードゲート) + 層2(軸) + カバレッジ** 構造へ再編。
- 旧 `Stage1/2/3` 後方互換キー・GATE 系キーは alert JSON で保持（既存テスト・外部参照を壊さない）。
- G4 の `FALLBACK_SCORE` / G6 の `required_documents` 空時の一律 100 を削除 → e_i=0 化。

### 5.2 `skills/grant_eligibility_checker/scoring_weights.json`（新規）
- 軸・重み・しきい値を外部化。コードはこの JSON を読み、無ければデフォルト値。
（将来は DB テーブル `scoring_weights` 化も視野。今回はファイル化で最小限。）

### 5.3 `skills/jgrants_search/scripts/extract_pdf.py`（追記）
- 構造化抽出に **`requirement_sentences` の決定論的抽出＆grants 保存** を追加（`req_rag` 軸の入力）。
- PDF ダウンロードの URL を得るため、attach url の収集ヘルパーを整備。

### 5.4 `scripts/harvest_grant_pdfs.py`（新規・Phase B トリガー）
- `detail_text` の `<a href>` から PDF を集めて `attachment_urls` に投入 → `extract_pdf.py` を起動 → `check_eligibility` を再実行（PROVISIONAL→確定へ）。

### 5.5 `tests/`（追加）
- 各軸の機械/RAG ロジック、カバレッジ計算、**データ欠落時に補完しない**挙動、PROVISIONAL 判定。
- 深掘り後の coverage 上昇と状態遷移の統合テスト。

### 5.6 `docs/`（整合）
- `grant_eligibility_checker/spec.md` / `SKILL.md` の**旧 3段階・17項目記述を 2層モデルに更新**（古いロジックのドキュメントを解消）。
- `grant_pipeline_spec.md` §6 を段階化フローに追記。

---

## 6. 導入順（スコープ分割）

| Step | 内容 | 成果物 |
|---|---|---|
| S1 | 軸モデル＋カバレッジ算出を check_eligibility に実装（ルール軸から）、重みJSON新規 | 再設計スコアの下地 |
| S2 | 状態付与（PROVISIONAL/ELIGIBLE/CONDITIONAL/INELIGIBLE）＋ alert 拡張 | カバレッジ考慮の判定 |
| S3 | deep-dive トリガー（PDF収集→extract→requirement_sentences追加→再スコア） | Phase B→C の閉ループ |
| S4 | テスト追加・docs/spec 更新・実データ1件(例 grant 20 農水省)で実スコア検証 | 検証 & 文書整合 |

---

## 7. 検証方法

- `env -u PYTHONPATH uv run pytest` — 全PASS
- `npx @naoya.k/spaghetti-guard check` — PASS
- `.spaghetti-guard/verify_model.py` — bge-m3 1024D
- **実データ**: grant 20（農水省 中山間）で PDF 収集→extract→再スコアを行い、PROVISIONAL→確定の遷移と RAG スコアの妥当性を確認

---

## 8. リスク・未確定事項

| 項目 | 内容 | 対応方針 |
|---|---|---|
| `requirement_sentences` 抽出精度 | 自動抽出（機械 or LLM）の精度 | まず機械+正規表現、精度不足時のみ LLM 引用(Substring Guard) |
| LLM定性(qual) | 外部 API 依存・コスト | 既定向け OFF。フラグ `--use-llm-qual` で明示有効化 |
| 重みの初期値 | 仮値 | 実データキャリブレーションで調整。JSON でチューニング可能に |
| 既存テスト/外部参照 | Stage1/2/3 後方互換 | alert JSON で旧キー保持、テストを新仕様へ更新 |

---

## 9. 合意確認

本計画の実装（S1→S2→S3→S4）を進めてよいか。
特に、**S3 の実データ深掘り（例: grant 20 で PDF 取得・RAG 投入・再スコア）** は実環境への副作用があるため、ここでの合意をもって実施します。

---

## 10. 抜け・漏れの補完（レビュー反映・2026-08-05）

### B. NPO 側実績ベクトルの取込（追加 Step）
`req_rag` / `sem_*` 軸の精度は NPO 側 `npo_knowledge_chunks` の質・量に依存する（現状 3 chunk）。**S3 に NPO 実績文の取込を追加**:
- `npo_profiles` には成果・実績が 1 フィールドに固まりがち → 実績テキスト（過去事業・採択実績・規模感）を分割して `npo_knowledge_chunks` へ追加取り込みする `ingest_npo_evidence` を計画に含める。
- これにより要件文→実績 RAG（`req_rag`）の弁別力が向上する。

### C. 深掘り失敗・OCR のハンドリング（明記）
- 画像 PDF（テキスト抽出 <100字）は OCR が必要。`Surya`/`tesseract` 未導入時は、その grant の深掘りを **失敗扱い** にし、対象軸は `e_i=0` のまま残す。
- `harvest_grant_pdfs.py` に `--force`（再実行）と手動フラグ `deep_dive_manual = true`（人が通した場合スキップ）を持たせる。

### D. スコアの意味とキャリブレーション（追加）
- 絶対スコアは「通る確率」ではない。**相対・比較のヒューリスティック** と定義。
- 閾値(≥75/55)は初期値。**`grant_past_awards`（過年度の採択/不採択）を ground truth にキャリブレーション**する手順を S4 に追加（例: スコアと採択率の相関・AUC 確認 → 閾値/重み調整）。
- キャリブレーション完了までは、alert にスコアと併せて `coverage` と「相対評価」である旨を表示する。

### E. Telegram 通知ポリシーとの連動（ブリッジ側の変更）
- 低 coverage の `PROVISIONAL` をそのまま通知すると、信頼性低い通知でスパム化する。
- **通知条件を明示**: `notify` は既定で `overall_status IN (ELIGIBLE, CONDITIONAL)` かつ `coverage ≥ 0.6` のみ。`--include-provisional` で例外的に出す。
- （`telegram_grant_bridge` の `notify` にフィルタ条件を反映する）

### F. 要件充足スコア と 勝率予測 の役割境界（整理）
| スコア | 担当 | 用途 |
|---|---|---|
| 要件充足（本計画・層1+層2） | eligibility_checker | 「応募して良いか＝書類/資格/要件を満たすか」 |
| 採択勝率（8軸・Win-rate） | grant_lifecycle_manager (未実装) | 「満たした上で、当たるか＝競争勝率」 |
- 二重実装せず、**要件充足は gate、勝率は rank として併用**する。今回の S1-S4 は要件充足のみ。

### G. 再スコア対象の自動選択（S3 の詳細化）
- 深掘り完了後、`alerts.report_json` に `unevaluated_axes` を持つ**既存 alert を対象**に再スコアする。
- 選択クエリ: `WHERE report_json->>'overall_status' = 'PROVISIONAL'`（または `unevaluated_axes` 非空）→ `harvest_grant_pdfs` → `extract_pdf` → `check_eligibility` 再実行。

### H. 既存テストの後方互換（列挙）
リファクタで影響する既存テストを明示し、更新方針を定める:
- `tests/test_eligibility_checker.py`（Stage1/2/3, Gate2/5/6, extract_pdf を参照）
- `tests/test_form_filler.py`, `tests/test_expense_validator.py`, `tests/test_past_award_analyzer.py`
- 方針: 旧 `Stage*` / `GATE_*` のキーは alert JSON とモジュール後方互換エイリアスで維持。テストは新 2 層仕様へ更新。

### I. パフォーマンス（明記）
- チェッカーは 1 (org, grant) ずつ。大量 grant の一括評価(`--all-grants`)時は、層1ハードゲートで先に篩い、層2 は通過分のみ。
- `req_rag` の embed はバッチ化し、HNSW を利用（N+1 検索を避ける）。

### J. データの「正」＝公募要領 PDF（明記）
- `grants.detail_text` は jGrants API の **概要HTML**。正式な要件は**公募要領 PDF**。
- **確定スコアは公募要領 PDF から抽出した要件を基準**とする。概要は初期スクリーニング（Phase A / PROVISIONAL）のみ。
- `harvest_grant_pdfs` の PDF 収集は `detail_text` 内 `<a href>` 優先、取得不能時は公式ページ crawl（`extract_pdf.py --url`）へフォールバック。

---

## 11. 追加ギャップ（第2次レビュー反映・2026-08-05）

### K. 書類「取得リードタイム」の考慮
`docs` 軸は「現在の所持」しか見ない。納税証明書等は発行に日数が要るため、**「締切残日数」と「未所持書類の取得リードタイム」** を考慮する:
- `grants.deadline` と `npo_profiles.prepared_documents` 差分のうち、要時間の書類（`TAX_CERTIFICATE` 等）が締切前リードタイム内に取得不可能なら `docs` 軸を減点/WARN。
- `grant_lifecycle_manager` の「書類取得期限（締切21日前）」概念と連動（実装はフェーズ後、今回は方針のみ明記）。

### L. 再スコア後の通知伝播（E と連動）
`_upsert_alert` は `is_notified` をリセットしないため、**通知済み alert が再スコアで ELIGIBLE/CONDITIONAL に昇格しても再通知されない**。
- 方針: `overall_status` が変化した場合のみ `is_notified = FALSE` にリセットし、昇格時は再通知を許容する（降格時は通知しない）。
- `telegram_grant_bridge` の `notify` は `coverage≥0.6` かつ `ELIGIBLE/CONDITIONAL` のみ（§E）を維持しつつ、上記リセットに対応。

### M. キャリブレーションの現実性（D の限定）
`grant_past_awards` は現在 **2件のみ**で、スコア⇄採択率のキャリブレーションは統計的に不能。
- **前提**: キャリブレーションは `past_award_analyzer` による採択データ蓄積（数十件以上）を待って実施。
- それまではスコアは**相対・比較ヒューリスティック**（絶対確率と表記しない）。

### N. 深掘りコスト制御（I の拡張）
全 288 件を深掘りしない。**層1通過（候補）の中から１バッチあたり top-N（既定 N=5）のみ**深掘りする。
- `harvest_grant_pdfs.py` に `--top-n` を持たせ、実行時間・外部サイト負荷・robots.txt 遵守を担保。

### O. 深掘り前の不適合アラート抑制
現行は 0% の不適合 alert も作成されノイズになる。
- **層1 ハードゲート失敗は `alerts` を作らない**（または `alert_type='UNQUALIFIED'` で分離し、通知対象から除外）。
- これにより `alerts` は「検討に値する候補」のみが積まれ、Telegram 通知(§E)のノイズも減る。

### P. detail_text の HTML 解析堅牢性（J の詳細化）
- `detail_text` は jGrants の HTML 断片。PDF リンクは **相対URL/JS描画/リンクなし** の場合がある。
- `BeautifulSoup` で `<a href>` を確定抽出し、`http(s)://` に正規化。取得不能 / 外部アンチボット先は `ingest_embedding_ui_spec` の robots.txt 遵守＋crawler レジリエンス（必要時 Camoufox）へフォールバック。

### Q. スコアリング履歴・版管理（監査）
深掘りで `report_json` が上書きされると「スコアがなぜ PROVISIONAL→ELIGIBLE に変わったか」追跡不能。
- 方針: `scoring_runs`（履歴）テーブル追加、または `alerts.report_json` 内に `previous_report` / `version` を保持。
- 低コストなら後者（`version` インクリメント）を先行採用。

---

## 12. スコープ明示（対象外の確認）

本計画（S1–S4）は**スコアリング（要件充足）のみ**。以下は対象外として『別計画』に切り分ける:
- **申請書自動記入・提出**（officecli による公式様式記入、メール/Web 提出）→ `docs/officecli_form_filling_spec.md` 整備（別途）
- **採択勝率予測（8軸 Win-rate）** → `grant_lifecycle_manager`（別スキル）
- **Gmail 監視・採択結果リマインド** → 別途

---

## 13. 全体横断・他計画連携のギャップ（第3次レビュー反映・2026-08-05）

スコアリング計画の外側も含め、パイプライン全体（検索→cron→通知→アプライ→フォロー→採択）の追加ギャップを整理する。

### R. 深掘り/抽出結果の鮮度管理（スコア S3）
`sync_grants_cron` の Upsert は `required_documents` / `knowledge_chunks` / `grant_expense_rules` 等を**保全のため上書きしない**。一方で新着概要更新（期限延長・条件変更）が入ると、**過去に抽出した要件・書類が失効したまま残る**。
- 方針: `grants.updated_at` が抽出日時より新しい、または `detail_text` の内容が変わった場合に再抽出を促す「鮮度フラグ」（例 `needs_re_extract`）を導入。

### S. 通知 UX：スヌーズ / ダイジェスト（Telegram 計画）
- 「🕐 あとで再通知」ボタン（スヌーズ）が無い（現行: 検討中追加/対象外/書類/企画書のみ）。
- 複数候補を 6 通連打でなく、**日次ダイジェスト（1通に凝縮）** にする任意モードを追加検討。

### T. 企画書起稿の冪等性（Telegram 計画）
ボタン連打・再実行で企画書/出力の**重複生成**が起き得る。
- 起稿前に既存 `grant_proposals` / `proposal_grant_mappings`（同 grant）を確認し、既にあれば追記・スキップする。

### U. アプライ後のステータス遷移の所有（Gmail/採択計画）
`proposal_grant_mappings.status`（CONSIDERING→APPLIED→ADOPTED/REJECTED）を**自動遷移させる実体が未定義**。採択結果リマインドの起点。
- Gmail 監視（別計画）または手動入力で更新するオーナーを定める。

### V. スコアの位置づけ＝ガードレール（スコア）
スコアは**支援材料**であり、最終的な応募可否・必須書類の権威は**公募要領 PDF 原文＋人判断**。
- alert/通知に「スコアは参考。最終判断は公募要領原文を確認」を明記し、誤った絶対化・自動錯雑を防ぐ（grant-research スキルの accuracy 規則とも整合）。

### W. PDF 保存先と監査連携（スコア S3）
深掘りで DL した公募要領 PDF の**保存場所・管理が未定義**。
- 保存先（例 `.output/grants/{grant_id}/`）を定め、`proposal_audit_evidences.evidence_type='AGREEMENT_PDF'` 等で証跡リンクを張る。

### X. Telegram 送信の信頼性（Telegram 計画）
`sendMessage` 失敗時の**リトライ/バックオフ**が無い（現在はログのみ）。
- 通知基盤として指数バックオフ付きリトライ（例 最大3回）を `telegram_bridge.send_message` に追加。

---

## 14. 全体ロードマップ（各計画の俯瞰）

| 計画 | 内容 | 状態 |
|---|---|---|
| **A. DB 整備** | Gate5/alert マイグレ適用 | ✅ 済（本セッション） |
| **B. cron 差分同期** | `sync_grants_cron.py` | ✅ 実装・dry-run 済 |
| **C. Telegram ブリッジ** | 通知＋ボタン | 🟢 ライブ稼働（UX改善 §S/T/X は次） |
| **D. スコアリング再設計** | 2層＋カバレッジ（本計画 S1–S4） | 📋 本計画 |
| **E. アプライ/officecli** | 公式様式記入・提出 | 📋 `officecli_form_filling_spec.md` 整備（別途） |
| **F. Gmail 監視・採択** | フォロー/採択リマインド | 📋 別途 |
| **G. 勝率予測** | 8軸 Win-rate | 📋 `grant_lifecycle_manager`（別スキル） |

本計画（D）は B・C と連動しつつ、確定した要件充足スコアを C の通知に渡す。E/F/G は独立して進められる。

---

## 15. 実装実務レベルの補完（第4次レビュー反映・2026-08-05）

### Y. Embedding モデルの多重ロード回避
`ingest_npo_profile.py` / `extract_pdf.py` / `check_eligibility.py` が**それぞれ `SentenceTransformer("BAAI/bge-m3")` を別々にロード**する。バッチ（cron / deep-dive）実行時にメモリ・起動が3倍になる。
- 方針: 共通の「embedding provider」モジュール（遅延ロード・プロセス内シングルトン）を新設し、3スクリプトが共有する。`normalize_embeddings=True` を一元化。

### Z. 抽出マスターの網羅性（深掘りカバレッジ上限）
`STANDARD_DOC_MASTERS`（6種）・`EXPENSE_PATTERNS`（5区分）しかないため、公募要領に無い名称の書類・経費は**検出漏れ**になり、`docs` / `expense` 軸のカバレッジが上がらない。
- 方針: マスターを拡張（寄付行為・誓約書・推薦状等）＋**マスター外らしき候補の原文フラグ**（例 `docs` 軸に「未マップ候補あり→WARN」）を追加。

### AA. スコア → アクション提示（人向け出力）
スコア・coverage の数値だけでは、団体が「次に何をすべきか」が分からない。
- `report_json` に `next_actions`（top-3 弱み軸、未取得書類、締切までの推奨アクション）を追加。alert/Telegram 通知に要約を出す。

### AB. RAG 軸のテスト戦略
新軸（`sem_*` / `req_rag`）は実DB＋モデル依存。現在のユニットテストは Dict モックのみ。
- 方針: 機械軸（budget/docs/expense）はユニットテスト、RAG 軸は**モック(固定ベクトル)でロジック検証＋実DBは明示のインテグレーション**に分離する（`@pytest.mark.e2e` を適用し既定除外）。

### AC. 正本 docs の一括整合
AGENTS.md 上では `docs/` が正本。`api_contract.md` / `specifications.md` に**旧3段階・`Stage1/2/3` 記述**が残っている可能性が高い。
- S4 で `check_eligibility` の改修と併せて、**全正本（spec / api_contract / SKILL / 本計画）を一括整合**する。
