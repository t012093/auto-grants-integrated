#!/usr/bin/env python3
"""
apply_missing_gate5_alert_migrations.py

検証で判明した Neon DB 未適用マイグレーション 2 件を適用する。
  * 20260802_add_gate5_requirement_sentences.sql
      - grants.requirement_sentences  (Gate5 特定要件 RAG 用)
      - alerts.report_json
  * 20260803_add_6gate_alert_fields.sql
      - alerts.overall_status, alerts.failed_gate_codes

どちらも `ADD COLUMN IF NOT EXISTS` なので冪等。適用後に列存在を検証する。

usage:
  uv run scripts/apply_missing_gate5_alert_migrations.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import psycopg
import psycopg.rows

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("❌ Error: DATABASE_URL is not set in .env file.")
    sys.exit(1)

MIGRATIONS = [
    Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "20260802_add_gate5_requirement_sentences.sql",
    Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "20260803_add_6gate_alert_fields.sql",
]

EXPECTED_COLUMNS = {
    "grants": ["requirement_sentences"],
    "alerts": ["report_json", "overall_status", "failed_gate_codes"],
}


def main() -> None:
    for path in MIGRATIONS:
        if not path.exists():
            print(f"❌ Migration file not found: {path}")
            sys.exit(1)

    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for path in MIGRATIONS:
                print(f"📖 Applying: {path.name}")
                with open(path, "r", encoding="utf-8") as f:
                    cur.execute(f.read())
                print(f"   ✅ {path.name} applied")

            # ---------- verify ----------
            print("\n=== Verify columns ===")
            ok = True
            for table, cols in EXPECTED_COLUMNS.items():
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s;",
                    (table,),
                )
                present = {r["column_name"] for r in cur.fetchall()}
                for c in cols:
                    status = "✅" if c in present else "❌"
                    if c not in present:
                        ok = False
                    print(f"  {table}.{c}: {status}")

            if not ok:
                print("\n❌ 一部カラムが未作成です。適用に失敗した可能性があります。")
                sys.exit(1)
            print("\n✅ All expected columns present.")


if __name__ == "__main__":
    main()
