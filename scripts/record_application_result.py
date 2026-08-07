#!/usr/bin/env python3
"""
record_application_result.py — 自社の採択結果を grant_applications に記録 (Phase C・手動取込)

予測精度検証 (spec §7.2 / §7.3 hold-out) の土台となる、自社応募の採択/不採択結果を
public.grant_applications に記録する CLI。

- result: AWARDED(採択) / REJECTED(不採択) / PENDING(審査中)
- 同一 (npo_profile_id, grant_id) は冪等に上書き (result を更新)
- reject_reason: 不採択理由（任意）

usage:
  env -u PYTHONPATH uv run scripts/record_application_result.py \
    --org-id <uuid> --grant-id <id> --result AWARDED [--reason "予算不足"] [--json]

検証 E2E:
  env -u PYTHONPATH uv run scripts/record_application_result.py \
    --org-id <uuid> --grant-id <id> --result AWARDED
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import psycopg
import psycopg.rows
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

VALID_RESULTS = ("AWARDED", "REJECTED", "PENDING")


def record_result(db_url: str, org_id: str, grant_id: int, result: str,
                  reason: str = "", appeared_at: str = "", force: bool = False) -> dict:
    """grant_applications に採択結果を記録 (冪等 Upsert)。"""
    if result not in VALID_RESULTS:
        raise ValueError(f"result は {VALID_RESULTS} のいずれかを指定(AWARDED/REJECTED/PENDING): {result}")

    appeared = appeared_at or date.today().isoformat()

    with psycopg.connect(db_url) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # 既存行チェック
            cur.execute(
                "SELECT id, result FROM public.grant_applications "
                "WHERE npo_profile_id = %s AND grant_id = %s",
                (org_id, grant_id),
            )
            existing = cur.fetchone()

            if existing and existing["result"] == result and not force:
                return {"status": "no_change", "recorded": False,
                        "npo_profile_id": org_id, "grant_id": grant_id,
                        "result": result, "message": "既に同じ result で記録済み"}

            cur.execute(
                """
                INSERT INTO public.grant_applications
                  (npo_profile_id, grant_id, appeared_at, result, reject_reason)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (npo_profile_id, grant_id) DO UPDATE SET
                  appeared_at = EXCLUDED.appeared_at,
                  result = EXCLUDED.result,
                  reject_reason = EXCLUDED.reject_reason
                """,
                (org_id, grant_id, appeared, result, reason or None),
            )
            conn.commit()
            return {"status": "recorded", "recorded": True,
                    "npo_profile_id": org_id, "grant_id": grant_id,
                    "result": result, "appeared_at": appeared}


def main():
    parser = argparse.ArgumentParser(description="自社の採択結果を grant_applications に記録 (Phase C)")
    parser.add_argument("--org-id", required=True, help="NPO Profile UUID")
    parser.add_argument("--grant-id", required=True, type=int, help="助成金 ID")
    parser.add_argument("--result", required=True, choices=VALID_RESULTS,
                        help="AWARDED(採択) / REJECTED(不採択) / PENDING(審査中)")
    parser.add_argument("--reason", default="", help="不採択理由（任意）")
    parser.add_argument("--appeared-at", default="", help="申請提出日 (YYYY-MM-DD)。省略時は今日")
    parser.add_argument("--force", action="store_true", help="同一 result でも強制上書き")
    parser.add_argument("--json", action="store_true", help="JSON 出力")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("❌ Error: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    try:
        res = record_result(DATABASE_URL, args.org_id, args.grant_id, args.result,
                            reason=args.reason, appeared_at=args.appeared_at,
                            force=args.force)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        if res["status"] == "no_change":
            print(f"ℹ️ 既に記録済み: grant={args.grant_id} result={args.result}")
        else:
            icon = {"AWARDED": "🎉", "REJECTED": "❌", "PENDING": "⏳"}[args.result]
            print(f"{icon} 採択結果を記録: grant={args.grant_id} result={args.result}")


if __name__ == "__main__":
    main()
