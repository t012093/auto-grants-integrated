#!/usr/bin/env python3
import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")

def main():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set in environment variables.")

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
            print("Registered updated_at Triggers:")
            for tgname, relname in rows:
                print(f"  - {tgname} on {relname}")

if __name__ == "__main__":
    main()
