#!/usr/bin/env python3
"""
predict_win_rate.py — 助成金 8軸採択予測 (rank A/B/C/D)  ※ spec §1-§9

grant_lifecycle_manager の 8軸採択予測を実装する。
- gate 確認: 既存 alerts.overall_status を参照し、gate 通過 (ELIGIBLE/CONDITIONAL) のみ rank を出す。
  (spec §9.2: INELIGIBLE/PROVISIONAL/alert無しは遮断 -> not_eligible)
- 再利用軸: eligibility report の axes (sem_purpose->funder_intent, budget, expense) を流用。
- 新規軸(rule): uniqueness / feasibility / sustainability / severity / scalability / track_record を
  確定的ルールで算出 (LLM定性は次のPhase)。
- 出力: overall_score(0-100) / coverage(0-1) / rank(A-D) / provisional / model_version
- 冪等: 既存 grant_win_rank があり・入力前提不変なら再計算しない (spec §9.3)。

usage:
  env -u PYTHONPATH uv run scripts/predict_win_rate.py \
       --org-id <uuid> --grant-id <id> [--json] [--force-recompute]
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
import psycopg.rows
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 重み定義の読み込み (config JSON 駆動・コード直書き禁止, spec §4.1)
# ---------------------------------------------------------------------------
_SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "grant_lifecycle_manager"
_WEIGHTS_PATH = _SKILL_DIR / "win_rate_weights.json"

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("predict_win_rate")


def load_weights(path: Optional[str] = None) -> Dict[str, Any]:
    """win_rate_weights.json を読み込み、version と axes 定義を返す。"""
    p = Path(path) if path else _WEIGHTS_PATH
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg


# ---------------------------------------------------------------------------
# 8軸計算 (確定的ルール)
# ---------------------------------------------------------------------------
def _score_uniqueness(npo: Dict, grant: Dict) -> Dict:
    """新規性: 企画書/活動に 新規/先進/モデル/革新 等の要素記述があるか (段階点)。"""
    keywords = ["新規", "先進", "モデル", "革新", "先駆"]
    # detail_text と description 双方を探索
    hay = f"{grant.get('detail_text') or ''} {npo.get('description') or ''}"
    hits = sum(1 for k in keywords if k in hay)
    if hits >= 3:
        return {"score": 1.0, "evaluated": True, "source": "rule",
                "evidence": f"先駆的要素語 {hits}件検出"}
    if hits >= 1:
        return {"score": 0.6, "evaluated": True, "source": "rule",
                "evidence": f"先駆的要素語 {hits}件検出"}
    # 記述が無い(但し全く情報が無ければ未評価)
    if not hay.strip():
        return {"score": None, "evaluated": False, "source": "rule",
                "reason": "企画書/活動記述なし"}
    return {"score": 0.3, "evaluated": True, "source": "rule",
            "evidence": "先駆的要素語なし"}


def _score_feasibility(npo: Dict, grant: Dict) -> Dict:
    """実現可能性: 連携/後援/体制記載の有無・数 (段階点)。"""
    keywords = ["連携", "協働", "パートナー", "後援", "行政", "体制"]
    hay = f"{grant.get('detail_text') or ''} {npo.get('description') or ''}"
    hits = sum(1 for k in keywords if k in hay)
    if hits >= 4:
        return {"score": 1.0, "evaluated": True, "source": "rule",
                "evidence": f"連携・体制語 {hits}件検出"}
    if hits >= 2:
        return {"score": 0.7, "evaluated": True, "source": "rule",
                "evidence": f"連携・体制語 {hits}件検出"}
    if hits >= 1:
        return {"score": 0.5, "evaluated": True, "source": "rule",
                "evidence": f"連携・体制語 {hits}件検出"}
    if not hay.strip():
        return {"score": None, "evaluated": False, "source": "rule",
                "reason": "企画書/活動記述なし"}
    return {"score": 0.3, "evaluated": True, "source": "rule",
            "evidence": "連携・体制語なし"}


def _score_sustainability(npo: Dict, grant: Dict) -> Dict:
    """自走性: 1-(amount_max / annual_budget) 基準 + 継続記述で加点。"""
    amount_max = grant.get("amount_max") or 0
    annual = npo.get("annual_budget") or 0
    if not annual or annual <= 0:
        return {"score": None, "evaluated": False, "source": "rule",
                "reason": "annual_budget 未設定"}
    ratio = 1.0 - (amount_max / annual) if amount_max and amount_max <= annual else 0.1
    ratio = max(0.0, min(1.0, ratio))
    # 継続記述ありで加点
    hay = f"{grant.get('detail_text') or ''} {npo.get('description') or ''}"
    if any(k in hay for k in ("継続", "自走", "自立")):
        ratio = min(1.0, ratio + 0.1)
    return {"score": round(ratio, 4), "evaluated": True, "source": "rule",
            "evidence": f"自走比率 {ratio:.2f} (amount_max={amount_max}, annual={annual})"}


def _score_severity(npo: Dict, grant: Dict) -> Dict:
    """深刻さ: 統計/アンケート/当事者ニーズ記述の有無 (段階点)。"""
    keywords = ["統計", "アンケート", "調査", "データ", "当事者", "ニーズ"]
    hay = f"{grant.get('detail_text') or ''} {npo.get('description') or ''}"
    hits = sum(1 for k in keywords if k in hay)
    if hits >= 3:
        return {"score": 1.0, "evaluated": True, "source": "rule",
                "evidence": f"エビデンス語 {hits}件検出"}
    if hits >= 1:
        return {"score": 0.6, "evaluated": True, "source": "rule",
                "evidence": f"エビデンス語 {hits}件検出"}
    if not hay.strip():
        return {"score": None, "evaluated": False, "source": "rule",
                "reason": "企画書/活動記述なし"}
    return {"score": 0.3, "evaluated": True, "source": "rule",
            "evidence": "エビデンス語なし"}


def _score_scalability(npo: Dict, grant: Dict) -> Dict:
    """インパクト: target_audience 数 × activity_areas 数 を基準に正規化。"""
    ta = len(npo.get("target_audience") or [])
    areas = npo.get("activity_areas") or npo.get("activity_tags") or []
    n_areas = len(areas)
    denom = max(1, ta * max(1, n_areas))
    score = min(1.0, denom / 8.0)  # 8 = 概ねスケール上限の目安
    return {"score": round(score, 4), "evaluated": True, "source": "rule",
            "evidence": f"対象層{ta}×分野{n_areas}"}


def _score_track_record(npo: Dict) -> Dict:
    """自社信用: track_records(入力JSON) or 実績数から算出。データ無ければ未評価。"""
    trs = npo.get("track_records") or []
    if isinstance(trs, str):
        try:
            trs = json.loads(trs)
        except Exception:
            trs = []
    if isinstance(trs, list) and trs:
        n = len(trs)
        score = min(1.0, 0.3 + n * 0.15)  # 1件=0.45, 3件+で1.0
        return {"score": round(score, 4), "evaluated": True, "source": "track_records",
                "evidence": f"track_records {n}件"}
    # grant_applications の AWARDED 実績数 (#は run() で注入)
    awarded = npo.get("_awarded_count") or 0
    if awarded:
        score = min(1.0, 0.3 + awarded * 0.15)
        return {"score": round(score, 4), "evaluated": True, "source": "grant_applications",
                "evidence": f"採択実績 {awarded}件"}
    return {"score": None, "evaluated": False, "source": "track_records",
            "reason": "自社実績データなし"}


def score_axis(code: str, npo: Dict, grant: Dict, eligibility_axes: Dict) -> Dict:
    """軸別スコア。再利用軸は eligibility report から、新規軸はルールで。"""
    if code == "funder_intent":
        # 再利用: eligibility sem_purpose + (任意) 過去採択ベンチマーク加味
        sem = eligibility_axes.get("sem_purpose") or {}
        if sem.get("evaluated"):
            s = float(sem["score"])
            return {"score": round(s, 4), "evaluated": True, "source": "eligibility/sem_purpose",
                    "evidence": sem.get("evidence") or ""}
        return {"score": None, "evaluated": False, "source": "eligibility/sem_purpose",
                "reason": "sem_purpose 未評価"}
    if code == "budget":
        # 再利用: eligibility budget / expense (積算の妥当性=Solver検証済み相当)
        budget = eligibility_axes.get("budget") or {}
        if budget.get("evaluated"):
            s = float(budget["score"])
            return {"score": round(s, 4), "evaluated": True, "source": "eligibility/budget",
                    "evidence": budget.get("evidence") or ""}
        expense = eligibility_axes.get("expense") or {}
        if expense.get("evaluated"):
            s = float(expense["score"])
            return {"score": round(s, 4), "evaluated": True, "source": "eligibility/expense",
                    "evidence": expense.get("evidence") or ""}
        return {"score": None, "evaluated": False, "source": "eligibility/budget",
                "reason": "積算軸未評価"}
    if code == "uniqueness":
        return _score_uniqueness(npo, grant)
    if code == "feasibility":
        return _score_feasibility(npo, grant)
    if code == "sustainability":
        return _score_sustainability(npo, grant)
    if code == "severity":
        return _score_severity(npo, grant)
    if code == "scalability":
        return _score_scalability(npo, grant)
    if code == "track_record":
        return _score_track_record(npo)
    return {"score": None, "evaluated": False, "source": "unknown", "reason": f"未知の軸: {code}"}


# ---------------------------------------------------------------------------
# 総合スコア & ランク
# ---------------------------------------------------------------------------
def compute_rank(weights: Dict, axes_scores: Dict[str, Dict]) -> Dict[str, Any]:
    """評価済み軸のみの重み付き平均から、coverage / overall_score / rank / provisional を算出。"""
    axes_def = weights["axes"]
    coverage_threshold = weights.get("coverage_threshold", 0.5)
    rank_th = weights.get("rank_thresholds", {"A": 80, "B": 65, "C": 50, "D": 0})

    w_sum = 0.0
    num = 0.0
    evaluated_codes = []
    for code, axdef in axes_def.items():
        s = axes_scores.get(code) or {}
        if s.get("evaluated") and s.get("score") is not None:
            w = axdef["weight"]
            w_sum += w
            num += w * float(s["score"])
            evaluated_codes.append(code)

    coverage = w_sum
    if w_sum <= 0:
        return {"overall_score": 0, "coverage": 0.0, "rank": None,
                "provisional": True, "evaluated_axes": [], "total_weight": 0.0,
                "insufficient_data": True}

    overall = int(round(num / w_sum * 100))
    # ランク
    def _rank(score: int) -> str:
        for r in ("A", "B", "C", "D"):
            if score >= rank_th[r]:
                return r
        return "D"

    rk = _rank(overall)
    provisional = coverage < coverage_threshold
    return {"overall_score": overall, "coverage": round(coverage, 4), "rank": rk,
            "provisional": provisional, "evaluated_axes": evaluated_codes,
            "total_weight": round(w_sum, 4), "insufficient_data": False}


def build_improvement_notes(axes_scores: Dict[str, Dict]) -> List[Dict]:
    """下位3軸を抽出し、弱点改善注記を生成 (数値創作はせず定性のみ)。"""
    ev = [(code, ax.get("score", 0)) for code, ax in axes_scores.items()
          if ax.get("evaluated") and ax.get("score") is not None]
    ev.sort(key=lambda x: x[1])
    weak = ev[:3]
    axis_label = {
        "uniqueness": "新規性", "feasibility": "実現体制", "sustainability": "自走性",
        "severity": "課題の深刻さ", "scalability": "インパクト", "budget": "積算",
        "funder_intent": "趣旨適合", "track_record": "自社信用",
    }
    notes = []
    for code, _sc in weak:
        if code == "sustainability":
            notes.append({"axis": code, "note": "「事業継続・自主事業化計画」節を企画書に追記を推奨"})
        elif code == "feasibility":
            notes.append({"axis": code, "note": "実施体制に連携先追加（後援名義）を推奨"})
        elif code == "severity":
            notes.append({"axis": code, "note": "事業背景に統計・当事者ニーズのエビデンス引用を推奨"})
        else:
            notes.append({"axis": code, "note": f"{axis_label.get(code, code)}の補強を推奨"})
    return notes


def gate_status_from_alerts(cur, npo_id: str, grant_id: int) -> Optional[Dict]:
    """alerts テーブルから gate 状態を取得 (spec §9.2)。無ければ None。"""
    cur.execute(
        "SELECT overall_status FROM public.alerts "
        "WHERE npo_profile_id = %s AND grant_id = %s ORDER BY created_at DESC LIMIT 1",
        (npo_id, grant_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"overall_status": row["overall_status"]}


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
class PredictWinRate:
    def __init__(self, db_url: Optional[str] = None, weights: Optional[Dict] = None):
        self.db_url = db_url or DATABASE_URL
        self.weights = weights or load_weights()

    def run(self, org_id: str, grant_id: int, force_recompute: bool = False,
            npo: Optional[Dict] = None, grant: Optional[Dict] = None,
            eligibility_report: Optional[Dict] = None) -> Dict[str, Any]:
        if not self.db_url:
            raise RuntimeError("DATABASE_URL is not set.")
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                # 1. 冪等チェック (spec §9.3)
                if not force_recompute:
                    cur.execute(
                        "SELECT model_version FROM public.grant_win_rank "
                        "WHERE npo_profile_id = %s AND grant_id = %s",
                        (org_id, grant_id),
                    )
                    existing = cur.fetchone()
                    if existing and existing["model_version"] == str(self.weights.get("version", "")):
                        cur.execute(
                            "SELECT overall_score, coverage, rank, provisional, axes_json, improvement_notes, model_version "
                            "FROM public.grant_win_rank WHERE npo_profile_id = %s AND grant_id = %s",
                            (org_id, grant_id),
                        )
                        row = cur.fetchone()
                        return {"cached": True, "npo_profile_id": org_id, "grant_id": grant_id,
                                "overall_score": row["overall_score"], "coverage": row["coverage"],
                                "rank": row["rank"], "provisional": row["provisional"],
                                "axes": row["axes_json"], "improvement_notes": row["improvement_notes"],
                                "model_version": row["model_version"]}

                # 2. gate 確認 (spec §9.2) — 未実施なら eligibility を再実行して status を得る
                gate = gate_status_from_alerts(cur, org_id, grant_id)
                status = gate["overall_status"] if gate else None
                if status not in ("ELIGIBLE", "CONDITIONAL"):
                    report = eligibility_report
                    if report is None:
                        raise RuntimeError(
                            "gate 未通過 or 未実施 (alerts に ELIGIBLE/CONDITIONAL なし)。"
                            "先に eligibility_v2.py を実行して gate を確定してください。"
                        )
                # 3. npo / grant を読み込み
                if npo is None:
                    cur.execute(
                        "SELECT * FROM public.npo_profiles WHERE id = %s", (org_id,))
                    npo = cur.fetchone()
                if grant is None:
                    cur.execute(
                        "SELECT * FROM public.grants WHERE id = %s", (grant_id,))
                    grant = cur.fetchone()
                if npo is None or grant is None:
                    raise RuntimeError(f"npo_profile({org_id}) or grant({grant_id}) not found")

                # 4. 採択実績数 (自社信用 for track_record)
                cur.execute(
                    "SELECT COUNT(*) AS n FROM public.grant_applications "
                    "WHERE npo_profile_id = %s AND result = 'AWARDED'", (org_id,))
                npo = dict(npo)
                npo["_awarded_count"] = cur.fetchone()["n"]

                # 5. eligibility report (再利用軸) — 引数優先、無ければ alerts から
                if eligibility_report is None:
                    cur.execute(
                        "SELECT report_json FROM public.alerts "
                        "WHERE npo_profile_id = %s AND grant_id = %s ORDER BY created_at DESC LIMIT 1",
                        (org_id, grant_id))
                    rep_row = cur.fetchone()
                    if rep_row and rep_row["report_json"]:
                        eligibility_report = rep_row["report_json"]
                eligibility_axes = {}
                if eligibility_report:
                    eligibility_axes = eligibility_report.get("axes", {})

                # 6. 8軸算出
                axes_scores = {code: score_axis(code, npo, grant, eligibility_axes)
                               for code in self.weights["axes"].keys()}

                # 7. coverage / rank
                comp = compute_rank(self.weights, axes_scores)
                if comp["insufficient_data"]:
                    return {"npo_profile_id": org_id, "grant_id": grant_id,
                            "status": "insufficient_data",
                            "reason": "評価可能な軸が0件 (入力データ不足)"}

                notes = build_improvement_notes(axes_scores)

                # 8. Upsert
                model_version = str(self.weights.get("version", ""))
                cur.execute(
                    """
                    INSERT INTO public.grant_win_rank
                      (npo_profile_id, grant_id, overall_score, coverage, rank, provisional,
                       model_version, axes_json, improvement_notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (npo_profile_id, grant_id) DO UPDATE SET
                      overall_score = EXCLUDED.overall_score,
                      coverage = EXCLUDED.coverage,
                      rank = EXCLUDED.rank,
                      provisional = EXCLUDED.provisional,
                      model_version = EXCLUDED.model_version,
                      axes_json = EXCLUDED.axes_json,
                      improvement_notes = EXCLUDED.improvement_notes,
                      updated_at = NOW()
                    """,
                    (org_id, grant_id, comp["overall_score"], comp["coverage"], comp["rank"],
                     comp["provisional"], model_version,
                     json.dumps(axes_scores, ensure_ascii=False),
                     json.dumps(notes, ensure_ascii=False)),
                )
                conn.commit()
                return {"npo_profile_id": org_id, "grant_id": grant_id,
                        "status": "ranked",
                        "overall_score": comp["overall_score"], "coverage": comp["coverage"],
                        "rank": comp["rank"], "provisional": comp["provisional"],
                        "model_version": model_version,
                        "axes": axes_scores, "improvement_notes": notes}


def main():
    parser = argparse.ArgumentParser(description="助成金 8軸採択予測 (rank A/B/C/D)")
    parser.add_argument("--org-id", required=True, help="NPO プロファイル UUID")
    parser.add_argument("--grant-id", required=True, type=int, help="助成金 ID")
    parser.add_argument("--json", action="store_true", help="JSON 出力")
    parser.add_argument("--force-recompute", action="store_true", help="冪等スキップを無視して再計算")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("❌ Error: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    predictor = PredictWinRate(DATABASE_URL)
    result = predictor.run(args.org_id, args.grant_id, force_recompute=args.force_recompute)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("status") == "not_eligible":
            print(f"❌ gate 未通過: {result}")
        elif result.get("status") == "insufficient_data":
            print(f"⚠️ データ不足: {result['reason']}")
        else:
            print(f"🏆 競争順位: {result.get('rank')} | スコア: {result['overall_score']}点 "
                  f"| カバレッジ: {result['coverage']:.0%}"
                  f"{' (暫定)' if result.get('provisional') else ''}")
            for code, ax in result["axes"].items():
                mark = "✅" if ax.get("evaluated") else "⬜"
                print(f"  {mark} {code}: {ax.get('score')} ({ax.get('source', '')})")


if __name__ == "__main__":
    main()
