#!/usr/bin/env python3
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import psycopg

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")

EXPECTED_TRIGGERS = [
    "set_company_profiles_updated_at",
    "set_grants_updated_at",
    "set_members_updated_at",
    "set_npo_expense_pref_updated_at",
    "set_npo_profiles_updated_at",
    "set_profiles_updated_at",
    "set_projects_updated_at",
]


def main():
    if not DATABASE_URL:
        print("❌ Error: DATABASE_URL is not set in environment variables.", file=sys.stderr)
        sys.exit(1)

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT tgname, relname 
                    FROM pg_trigger t
                    JOIN pg_class c ON t.tgrelid = c.oid
                    WHERE tgname LIKE 'set_%_updated_at'
                    ORDER BY tgname;
                """)
                rows = cur.fetchall()
                found_triggers = [row[0] for row in rows]
                
                print(f"Registered updated_at Triggers ({len(found_triggers)} found):")
                for tgname, relname in rows:
                    print(f"  - {tgname} on {relname}")

                missing = set(EXPECTED_TRIGGERS) - set(found_triggers)
                if missing:
                    print(f"\n❌ Verification Failed! Missing triggers: {sorted(list(missing))}", file=sys.stderr)
                    sys.exit(1)
                
                print("\n✅ Trigger Verification Passed! All 7 triggers active.")
    except Exception as e:
        print(f"❌ Connection or verification error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

