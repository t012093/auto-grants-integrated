#!/usr/bin/env python3
"""
deep_dive_batch.py — 助成金深掘り一括実行 (スコアリング再設計 S4 / 計画 §N)

対象 OPEN 助成金に対し、grant 単位で以下を逐次実行する:
  1. harvest_grant_pdfs  → 公募要領PDF特定・attachment_urls登録
  2. extract_pdf         → PDF深掘り(書類/要件文/経費/ベクトル投入)
  3. eligibility_v2 re-score → coverage付き要件充足スコア

層1不合格(INELIGIBLE)はスキップ。各grantは失敗しても次へ進む(耐障害)。
usage:
  env -u PYTHONPATH uv run skills/jgrants_search/scripts/deep_dive_batch.py \
       --org-id <uuid> [--grant-ids 3,10,11] [--skip-extract]
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import psycopg.rows
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("deep_dive_batch")

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = ROOT / "skills" / "jgrants_search" / "scripts"
ELIG = ROOT / "skills" / "grant_eligibility_checker" / "scripts" / "eligibility_v2.py"


def run_script(py, args, timeout=420):
    """子プロセス実行。JSON(または最終行)を返す。失敗は Exception ではなく None で返す。"""
    r = subprocess.run([py, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        logger.warning("  実行失敗 rc=%s: %s", r.returncode, (r.stderr or r.stdout)[-300:])
        return None
    out = r.stdout.strip()
    idx = out.find("{")
    if idx < 0:
        return {"ok": "text", "raw": out[-200:]}
    try:
        return json.loads(out[idx:])
    except Exception:
        return {"ok": "text", "raw": out[-200:]}


def get_open_grant_ids(cur):
    cur.execute("SELECT id FROM public.grants WHERE status='OPEN' ORDER BY id;")
    return [r["id"] for r in cur.fetchall()]


def shallow_status(py, org, gid):
    d = run_script(py, [str(ELIG), "--org-id", org, "--grant-id", str(gid), "--json"], timeout=200) or {}
    return d


def main():
    parser = argparse.ArgumentParser(description="深掘り一括実行")
    parser.add_argument("--org-id", required=True)
    parser.add_argument("--grant-ids", help="カンマ区切り。省略で全OPEN")
    parser.add_argument("--skip-extract", action="store_true", help="harvest+re-scoreのみ(extract_pdfをスキップ)")
    parser.add_argument("--dry-run", action="store_true", help="実際のDB更新/外部取得をあらかじめ確認(harvest/extractは実行せず計画表示)")
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    py = sys.executable

    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            all_ids = get_open_grant_ids(cur)
    if args.grant_ids:
        ids = [int(x) for x in args.grant_ids.split(",")]
    else:
        ids = all_ids

    logger.info("対象 %s 件: %s", len(ids), ids)

    # 事前: 層1不合格の除外と、深掘りが未実施の grant を判定
    summary = []
    for gid in ids:
        d = shallow_status(py, args.org_id, gid)
        if not d:
            summary.append({"grant_id": gid, "stage": "shallow_err"})
            continue
        st = d.get("overall_status")
        if st == "INELIGIBLE":
            logger.info("grant %s: INELIGIBLE(層1) → スキップ", gid)
            summary.append({"grant_id": gid, "stage": "skip_ineligible", "status": st,
                            "failed": d.get("failed_gate_codes")})
            continue
        cov = d.get("coverage", 0)
        uneval = d.get("unevaluated_axes", [])
        if cov >= 0.6 and len(uneval) == 0:
            logger.info("grant %s: 既にcoverage%.0f%%・全軸評価済み → スキップ", gid, cov)
            summary.append({"grant_id": gid, "stage": "already_determined", "status": st, "coverage": cov})
            continue

        # --- 深掘り ---
        rec = {"grant_id": gid, "stage": "deep_dive", "before_status": st,
               "before_coverage": cov, "unevaluated_before": uneval}
        if not args.dry_run:
            # 1) harvest
            h = run_script(py, [str(SCRIPTS / "harvest_grant_pdfs.py"), "--grant-id", str(gid)], timeout=120)
            rec["harvest"] = (h or {}).get("selected_url") if h and h.get("ok") not in (0, False) else None
            if not (rec["harvest"]):
                # harvest 失敗 or 対象PDFなし → extract はスキップ
                logger.warning("grant %s: harvest で公募要領PDFを特定できず extract スキップ", gid)
                rec["extract"] = "no_pdf"
                # それでも再スコアは実施(既存データの反映)
            # 2) extract (PDF が取得できた grant のみ)
            if rec.get("harvest"):
                if not args.skip_extract:
                    ex = run_script(py, [str(SCRIPTS / "extract_pdf.py"), "--grant-id", str(gid), "--json"], timeout=420)
                    rec["extract"] = (ex or {}).get("status") if isinstance(ex, dict) else None
                    rec["extract_len"] = (ex or {}).get("extracted_text_len") if isinstance(ex, dict) else None
                else:
                    rec["extract"] = "skipped_by_flag"
            # 3) re-score
            d2 = shallow_status(py, args.org_id, gid)
            if d2:
                rec["after_status"] = d2.get("overall_status")
                rec["after_score"] = d2.get("match_score")
                rec["after_coverage"] = d2.get("coverage")
                rec["unevaluated_after"] = d2.get("unevaluated_axes")
        else:
            rec["dry_run"] = True
        summary.append(rec)
        logger.info("grant %s 完了: %s", gid, {k: rec.get(k) for k in
                     ("before_status", "harvest", "extract", "after_status", "after_coverage")})

    # 表形式サマリ
    print("\n================ 深掘りバッチ サマリ ================")
    print(f"{'id':>4} {'stage':<18} {'before':<12} {'harvest':<14} {'extract':<12} {'after':<12} cov")
    for r in summary:
        print(f"{r['grant_id']:>4} {r['stage']:<18} {str(r.get('before_status')):<12} "
              f"{str(r.get('harvest'))[:12]:<14} {str(r.get('extract')):<12} "
              f"{str(r.get('after_status')):<12} {r.get('after_coverage')}")
    out_path = ROOT / "artifacts_deepdive_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n詳細: {out_path}")


if __name__ == "__main__":
    main()
