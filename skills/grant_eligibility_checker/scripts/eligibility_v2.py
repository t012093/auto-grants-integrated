#!/usr/bin/env python3
"""
eligibility_v2.py — 助成金要件充足スコアラー (2層モデル)  ※ スコアリング再設計 S1/S2

docs/scoring_redesign_plan.md に基づき、check_eligibility.py の 6-Gate を
「層1(ハードエリジビリティゲート) + 層2(重み付き8軸・評価カバレッジ)」へ再設計したエントリ。

- 層1: E1法人格 / E2地域 / E3実績年数 / E4公募状態(OPEN・締切)。1つ不合格 → INELIGIBLE(alert非作成)。
- 層2: budget / sem_activity / sem_audience / sem_purpose / req_rag / docs / expense / qual
       各軸 (score, weight, evaluated)。evaluated=0 の軸は補完せず、重みを評価済み分で再正規化。
       coverage = 評価済み重み。coverage < 0.6 → PROVISIONAL(要深掘り)。
- alert: overall_status 変化時のみ is_notified をリセット(昇格再通知対応)。

usage:
  env -u PYTHONPATH uv run skills/grant_eligibility_checker/scripts/eligibility_v2.py \
       --org-id <uuid> --grant-id <id> [--json] [--use-llm-qual]
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg
import psycopg.rows
from dotenv import load_dotenv

# 同一ディレクトリの既存ゲート評価器を再利用(後方互換クラス)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_eligibility import (  # noqa: E402
    Gate1BasicRuleEvaluator,
    Gate2LocationEvaluator,
    Gate4SemanticEvaluator,
)

env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eligibility_v2")

# 既定設定 (scoring_weights.json が無ければ使用)
DEFAULT_CONFIG = {
    "coverage_threshold": 0.6,
    "thresholds": {"eligible": 75, "conditional": 55},
    "axes": {
        "budget": {"label": "予算規模適合", "weight": 0.10},
        "sem_activity": {"label": "活動分野セマンティック", "weight": 0.15},
        "sem_audience": {"label": "対象層セマンティック", "weight": 0.10},
        "sem_purpose": {"label": "事業目的一致性", "weight": 0.10},
        "req_rag": {"label": "特定要件RAG充足", "weight": 0.20},
        "docs": {"label": "書類準備率", "weight": 0.15},
        "expense": {"label": "経費ルール整合", "weight": 0.10},
        "qual": {"label": "定性ミッション合致", "weight": 0.10, "llm": True},
    },
}


class TwoLayerScorer:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.axes_config, self.coverage_threshold, self.thresholds = self._load_config()
        self._embedder = None

    # ------------------------------------------------------------------
    # 設定
    # ------------------------------------------------------------------
    def _load_config(self) -> Tuple[Dict, float, Dict]:
        cfg = None
        p = Path(__file__).resolve().parent.parent / "scoring_weights.json"
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception as e:
                logger.warning("scoring_weights.json 読込失敗(defaults使用): %s", e)
        if not cfg:
            cfg = DEFAULT_CONFIG
        axes = {code: {**meta, "weight": float(meta["weight"])} for code, meta in cfg["axes"].items()}
        return axes, float(cfg.get("coverage_threshold", 0.6)), cfg.get("thresholds", {"eligible": 75, "conditional": 55})

    def get_embedder(self):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer("BAAI/bge-m3")
        return self._embedder

    # ------------------------------------------------------------------
    # 層1: ハードエリジビリティゲート
    # ------------------------------------------------------------------
    def _hard_gates(self, npo: Dict, grant: Dict) -> List[Dict]:
        g1 = Gate1BasicRuleEvaluator.evaluate(npo, grant)   # E1/E3/E4 (organization_type/years_active/grant_status)
        g1d = g1.details
        g2 = Gate2LocationEvaluator.evaluate(npo, grant)    # E2
        def _p(d): return bool(d.get("pass"))
        gates = [
            {"code": "E1", "name": "対象法人格", "passed": _p(g1d.get("organization_type", {})),
             "reason": g1d.get("organization_type", {}).get("reason", "")},
            {"code": "E2", "name": "対象地域", "passed": g2.passed, "reason": g2.reason},
            {"code": "E3", "name": "実績年数", "passed": _p(g1d.get("years_active", {})),
             "reason": g1d.get("years_active", {}).get("reason", "")},
            {"code": "E4", "name": "公募状態(OPEN・締切)", "passed": _p(g1d.get("grant_status", {})),
             "reason": g1d.get("grant_status", {}).get("reason", "")},
        ]
        return gates

    # ------------------------------------------------------------------
    # 層2: 軸評価 (戻り: tuple (score_0to1|None, evaluated, evidence))
    # ------------------------------------------------------------------
    def _mech_budget(self, npo, grant):
        max_amt = grant.get("amount_max") or 0
        budget = npo.get("annual_budget") or 0
        if not (max_amt > 0 and budget > 0):
            return (None, False, "金額データ未設定")
        ratio = max_amt / budget
        score = 1.0 if ratio <= 0.5 else max(0.0, 1.0 - (ratio - 0.5) * 2.0)
        return (score, True, f"上限{int(max_amt):,}円/年予算{int(budget):,}円 (比率{ratio:.0%})")

    def _rag_sem(self, cur, npo_id: str, gid: int, ctype: str):
        score_int, quote = Gate4SemanticEvaluator._get_vector_similarity(cur, npo_id, gid, ctype)
        if quote is None:
            return (None, False, f"{ctype} RAGデータなし(要深掘り)")
        return (score_int / 100.0, True, (quote or "")[:120])

    def _rag_req(self, cur, npo_id: str, grant):
        reqs = grant.get("requirement_sentences") or []
        if not reqs:
            return (None, False, "要件文未抽出(要深掘り)")
        cur.execute("SELECT COUNT(*) AS c FROM public.npo_knowledge_chunks WHERE npo_profile_id=%s;", (npo_id,))
        r = cur.fetchone()
        ncount = (r["c"] if isinstance(r, dict) else r[0]) if r else 0
        if ncount == 0:
            return (0.0, True, "NPO実績ベクトル未登録(不足)")
        embedder = self.get_embedder()
        passed = 0
        for req in reqs[:20]:
            vec = embedder.encode([req], normalize_embeddings=True)[0]
            vstr = str(vec.tolist())
            cur.execute(
                "SELECT 1-(embedding<=>%s::vector) AS sim FROM public.npo_knowledge_chunks "
                "WHERE npo_profile_id=%s ORDER BY embedding<=>%s::vector LIMIT 1;",
                (vstr, npo_id, vstr),
            )
            row = cur.fetchone()
            sim = float(row["sim"] if isinstance(row, dict) else row[0]) if row else 0.0
            if sim >= 0.70:
                passed += 1
        return (passed / len(reqs), True, f"{passed}/{len(reqs)}要件で類似度>=0.70")

    def _mech_docs(self, npo, grant):
        req = grant.get("required_documents") or []
        if not req:
            return (None, False, "必要書類未抽出(要深掘り)")
        prep = set(npo.get("prepared_documents") or [])
        got = set(req) & prep
        missing = sorted(set(req) - prep)
        return (len(got) / len(req), True,
                f"準備 {len(got)}/{len(req)}" + (f" 不足:{','.join(missing)}" if missing else ""))

    def _mech_expense(self, cur, npo_id, grant):
        cur.execute("SELECT category_code, allowed FROM public.grant_expense_rules WHERE grant_id=%s;", (grant["id"],))
        rules = cur.fetchall()
        cur.execute("SELECT category_code, desired_amount FROM public.npo_expense_preferences WHERE npo_profile_id=%s;",
                    (npo_id,))
        prefs = cur.fetchall()
        if not rules or not prefs:
            return (None, False, "経費ルール/希望優先度未登録(要深掘り)")
        allowed = {}
        for r in rules:
            k = r["category_code"] if isinstance(r, dict) else r[0]
            v = r["allowed"] if isinstance(r, dict) else r[1]
            allowed[k] = bool(v)
        total = 0
        alloc = 0
        for p in prefs:
            cat = p["category_code"] if isinstance(p, dict) else p[0]
            d = p.get("desired_amount") if isinstance(p, dict) else p[1]
            d = d or 0
            total += d
            if allowed.get(cat, True):
                alloc += d
        ratio = alloc / total if total else 1.0
        return (ratio, True, f"配分可能 {alloc:,}/{total:,}")

    def _eval_axes(self, cur, npo, grant, use_llm_qual: bool) -> Dict[str, Dict]:
        npo_id = str(npo["id"])
        gid = grant["id"]
        ev: Dict[str, Tuple] = {
            "budget": self._mech_budget(npo, grant),
            "sem_activity": self._rag_sem(cur, npo_id, gid, "ACTIVITY_TAGS"),
            "sem_audience": self._rag_sem(cur, npo_id, gid, "TARGET_AUDIENCE"),
            "sem_purpose": self._rag_sem(cur, npo_id, gid, "DESCRIPTION"),
            "req_rag": self._rag_req(cur, npo_id, grant),
            "docs": self._mech_docs(npo, grant),
            "expense": self._mech_expense(cur, npo_id, grant),
            "qual": (None, False, "LLM定性は既定OFF" if not use_llm_qual else "LLM定性は未実装"),
        }
        axes: Dict[str, Dict] = {}
        for code, meta in self.axes_config.items():
            s, e, evidence = ev.get(code, (None, False, ""))
            axes[code] = {
                "label": meta.get("label", code),
                "weight": meta["weight"],
                "score": float(s) if s is not None else 0.0,
                "evaluated": bool(e),
                "evidence": evidence,
            }
        return axes

    # ------------------------------------------------------------------
    # 結果構築
    # ------------------------------------------------------------------
    def _next_actions(self, npo, grant, axes, status) -> List[str]:
        acts = []
        if status == "PROVISIONAL":
            acts.append("深掘りが必要: 公募要領PDFから書類/要件/経費を抽出して再スコア")
        low = sorted([(a["score"], a["label"]) for c, a in axes.items() if a["evaluated"]])[:3]
        for _, label in low:
            acts.append(f"弱み軸: {label}")
        req = grant.get("required_documents") or []
        if req:
            missing = sorted(set(req) - set(npo.get("prepared_documents") or []))
            if missing:
                acts.append(f"未取得書類: {', '.join(missing)}")
        if not acts:
            acts.append("全軸評価済み・特に不足なし")
        return acts

    def _build_report(self, npo, grant, hard_gates, axes, score100, coverage, status, failed, next_actions) -> Dict:
        def axis(code):
            return axes.get(code)

        b = axis("budget"); docs = axis("docs"); req = axis("req_rag")
        sem = [axes[c] for c in ("sem_activity", "sem_audience", "sem_purpose") if axes[c]["evaluated"]]
        sem_score = int(round(sum(a["score"] * 100 for a in sem) / len(sem))) if sem else 80

        g1_pass = all(hg["passed"] for hg in hard_gates if hg["code"] in ("E1", "E3", "E4"))
        gates = [
            {"gate_code": "GATE_1", "gate_name": "基礎(法人格/年数/公募)", "passed": g1_pass,
             "status": "PASS" if g1_pass else "FAIL", "score": 100 if g1_pass else 0,
             "details": {hg["code"]: {"pass": hg["passed"], "reason": hg["reason"]}
                         for hg in hard_gates if hg["code"] in ("E1", "E3", "E4")}},
            {"gate_code": "GATE_2", "gate_name": "拠点要件", "passed": hard_gates[1]["passed"],
             "status": "PASS" if hard_gates[1]["passed"] else "FAIL", "score": 100 if hard_gates[1]["passed"] else 0,
             "details": {"target_area": {"pass": hard_gates[1]["passed"], "reason": hard_gates[1]["reason"]}}},
            {"gate_code": "GATE_3", "gate_name": "予算規模適合", "passed": True, "status": "PASS",
             "score": int(round(b["score"] * 100)) if b and b["evaluated"] else 80,
             "details": {"budget_ratio": {"pass": bool(b and b["evaluated"]), "reason": b["evidence"] if b else "未評価"}}},
            {"gate_code": "GATE_4", "gate_name": "セマンティック適合", "passed": True, "status": "PASS",
             "score": sem_score,
             "details": {"criteria_scores": {c: int(round(axes[c]["score"] * 100)) for c in ("sem_activity", "sem_audience", "sem_purpose") if axes[c]["evaluated"]},
                         "evidence_quotes": [axes[c]["evidence"] for c in ("sem_activity", "sem_audience", "sem_purpose") if axes[c]["evaluated"] and axes[c].get("evidence")]}},
            {"gate_code": "GATE_5", "gate_name": "特定要件RAG", "passed": True,
             "status": "PASS" if (req and req["evaluated"]) else "SKIP",
             "score": int(round(req["score"] * 100)) if req and req["evaluated"] else 100,
             "details": {"reason": req["evidence"] if req else "要件文未抽出(スキップ)"}},
            {"gate_code": "GATE_6", "gate_name": "書類準備率", "passed": True,
             "status": "PASS" if (docs and docs["evaluated"]) else "SKIP",
             "score": int(round(docs["score"] * 100)) if docs and docs["evaluated"] else 100,
             "details": {"reason": docs["evidence"] if docs else "必要書類未抽出(未評価)",
                         "missing": sorted(set(grant.get("required_documents") or []) - set(npo.get("prepared_documents") or []))}},
        ]

        return {
            "grant_id": grant["id"], "grant_title": grant["title"],
            "npo_profile_id": str(npo["id"]), "npo_name": npo["name"],
            "match_score": score100, "overall_status": status, "status": status,
            "failed_gate_codes": failed,
            "coverage": round(coverage, 4),
            "total_score": score100,
            "axes": {c: {"label": a["label"], "score": round(a["score"], 4), "weight": a["weight"],
                         "evaluated": a["evaluated"], "evidence": a["evidence"]} for c, a in axes.items()},
            "unevaluated_axes": [c for c, a in axes.items() if not a["evaluated"]],
            "next_actions": next_actions,
            "hard_gates": hard_gates,
            "gates": gates,
            "evaluated_at": datetime.now().isoformat(),
            # 後方互換キー
            "stage1_results": {"all_pass": all(hg["passed"] for hg in hard_gates),
                               "details": {hg["code"]: {"pass": hg["passed"], "reason": hg["reason"]} for hg in hard_gates}},
            "stage2_results": {"score": int(round(docs["score"] * 100)) if docs and docs["evaluated"] else 100,
                               "required": list(grant.get("required_documents") or []),
                               "prepared": list(npo.get("prepared_documents") or []),
                               "missing": sorted(set(grant.get("required_documents") or []) - set(npo.get("prepared_documents") or []))},
            "stage3_results": {"score": sem_score, "criteria_scores": gates[3]["details"]["criteria_scores"],
                               "evidence_quotes": gates[3]["details"]["evidence_quotes"]},
        }

    def _upsert_alert(self, org_id, grant_id, title, score, status, failed, report):
        msg = f"要件充足: {score}% | カバレッジ {report['coverage']:.0%} | 判定: {status}"
        if report["unevaluated_axes"]:
            msg += f" | 未評価: {','.join(report['unevaluated_axes'])}"
        report_json = json.dumps(report, ensure_ascii=False, default=str)
        try:
            with psycopg.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT overall_status FROM public.alerts "
                        "WHERE npo_profile_id=%s AND grant_id=%s AND alert_type='ELIGIBILITY_MATCH';",
                        (org_id, grant_id),
                    )
                    row = cur.fetchone()
                    prev = row[0] if row else None
                    reset_notify = (prev is not None and prev != status)
                    cur.execute(
                        """
                        INSERT INTO public.alerts
                            (npo_profile_id, grant_id, alert_type, title, message, match_score,
                             is_read, is_notified, report_json, overall_status, failed_gate_codes)
                        VALUES (%s, %s, 'ELIGIBILITY_MATCH', %s, %s, %s, false, false, %s::jsonb, %s, %s)
                        ON CONFLICT ON CONSTRAINT uq_alerts_npo_grant_type DO UPDATE SET
                            title = EXCLUDED.title, message = EXCLUDED.message, match_score = EXCLUDED.match_score,
                            report_json = EXCLUDED.report_json, overall_status = EXCLUDED.overall_status,
                            failed_gate_codes = EXCLUDED.failed_gate_codes, is_read = false,
                            is_notified = CASE WHEN %s THEN FALSE ELSE alerts.is_notified END,
                            created_at = NOW();
                        """,
                        (org_id, grant_id, f"【{status}】{title}", msg, score, report_json, status, failed, reset_notify),
                    )
                conn.commit()
        except Exception as e:
            logger.warning("alert 保存失敗: %s", e)

    # ------------------------------------------------------------------
    # 実行
    # ------------------------------------------------------------------
    def run(self, org_id: str, grant_id: str, use_llm_qual: bool = False) -> Dict:
        with psycopg.connect(self.db_url, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM public.npo_profiles WHERE id = %s;", (org_id,))
                npo = cur.fetchone()
                if not npo:
                    raise ValueError(f"NPO Profile with ID '{org_id}' not found.")
                if str(grant_id).isdigit():
                    cur.execute("SELECT * FROM public.grants WHERE id = %s OR source_grant_id = %s;",
                                (int(grant_id), grant_id))
                else:
                    cur.execute("SELECT * FROM public.grants WHERE source_grant_id = %s;", (grant_id,))
                grant = cur.fetchone()
                if not grant:
                    raise ValueError(f"Grant with ID '{grant_id}' not found.")

                # 層1
                hard_gates = self._hard_gates(npo, grant)
                failed = [hg["code"] for hg in hard_gates if not hg["passed"]]

                # 層2
                axes = self._eval_axes(cur, npo, grant, use_llm_qual)

                # カバレッジ & 総合
                W = sum(a["weight"] for a in axes.values() if a["evaluated"])
                num = sum(a["weight"] * a["score"] for a in axes.values() if a["evaluated"])
                coverage = W
                total_ratio = num / W if W > 0 else 0.0
                score100 = int(round(total_ratio * 100))

                if failed:
                    status, persist = "INELIGIBLE", False
                    score100 = 0
                elif coverage < self.coverage_threshold:
                    status, persist = "PROVISIONAL", True
                elif score100 >= self.thresholds["eligible"]:
                    status, persist = "ELIGIBLE", True
                elif score100 >= self.thresholds["conditional"]:
                    status, persist = "CONDITIONAL", True
                else:
                    status, persist = "INELIGIBLE", True

                next_actions = self._next_actions(npo, grant, axes, status)
                report = self._build_report(npo, grant, hard_gates, axes, score100,
                                            coverage, status, failed, next_actions)

                if persist:
                    self._upsert_alert(str(npo["id"]), grant["id"], grant["title"],
                                       score100, status, failed, report)
                else:
                    logger.info("層1ハードゲート不合格(%s)のため alert は作成しません", ",".join(failed))
                return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="助成金要件充足スコアラー (2層モデル)")
    parser.add_argument("--org-id", required=True, help="NPO Profile UUID")
    parser.add_argument("--grant-id", required=True, help="Grant DB ID or source_grant_id")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--use-llm-qual", action="store_true", help="LLM定性軸を有効化(実験的)")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("❌ Error: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    scorer = TwoLayerScorer(DATABASE_URL)
    try:
        result = scorer.run(args.org_id, args.grant_id, use_llm_qual=args.use_llm_qual)
    except Exception as e:
        print(f"❌ Evaluation Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    icon = {"ELIGIBLE": "✅", "CONDITIONAL": "⚠️", "INELIGIBLE": "❌", "PROVISIONAL": "🟡"}.get(result["overall_status"], "❓")
    print("\n==================================================")
    print(" 助成金要件充足スコア (2層モデル)")
    print(f" 助成金: {result['grant_title']}")
    print(f" 団体名: {result['npo_name']}")
    print("==================================================")
    print(f" {icon} 判定: {result['overall_status']} | スコア: {result['match_score']}% | カバレッジ: {result['coverage']:.0%}")
    if result["failed_gate_codes"]:
        print(f" 不合格ゲート: {', '.join(result['failed_gate_codes'])}")

    print("\n [層1 ハードゲート]")
    for hg in result["hard_gates"]:
        m = "✅" if hg["passed"] else "❌"
        print(f"   {m} {hg['code']} {hg['name']}: {hg['reason']}")

    print("\n [層2 軸スコア]")
    for code, a in result["axes"].items():
        st = f"{a['score']*100:.0f}点" if a["evaluated"] else "未評価"
        print(f"   - {code} ({a['label']}, 重み{a['weight']:.0%}): {st}")

    if result["unevaluated_axes"]:
        print(f"\n [未評価(要深掘り)]: {', '.join(result['unevaluated_axes'])}")
    if result["next_actions"]:
        print("\n [次のアクション]")
        for a in result["next_actions"]:
            print(f"   → {a}")
    print("==================================================\n")


if __name__ == "__main__":
    main()
