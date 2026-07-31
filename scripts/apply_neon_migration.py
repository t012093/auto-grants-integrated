#!/usr/bin/env python3
"""
Neon Database Migration Script
Applies 20260731_init_neon_schema.sql to Neon PostgreSQL database safely.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import psycopg

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("❌ Error: DATABASE_URL is not set in .env file.")
    sys.exit(1)

sql_file_path = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "20260731_init_neon_schema.sql"

if not sql_file_path.exists():
    print(f"❌ Error: Migration SQL file not found at {sql_file_path}")
    sys.exit(1)


def main():
    print(f"📡 Connecting to Neon PostgreSQL...")
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            print("✅ Connection successful.")
            
            print(f"📖 Reading SQL migration file: {sql_file_path.name}")
            with open(sql_file_path, "r", encoding="utf-8") as f:
                sql_script = f.read()

            with conn.cursor() as cur:
                print("🚀 Executing DDL migration script...")
                cur.execute(sql_script)
                print("✅ All tables, types, indexes, and triggers created successfully!")
                
                # Verify tables
                cur.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public'
                    ORDER BY table_name;
                """)
                tables = [row[0] for row in cur.fetchall()]
                print(f"\n📋 Created Public Tables ({len(tables)} tables):")
                for t in tables:
                    print(f"  - public.{t}")

            conn.commit()
            print("\n✨ Neon Database setup completed successfully.")

    except Exception as e:
        print(f"❌ Error applying migration: Migration execution failed (Details hidden for security)")
        sys.exit(1)


if __name__ == "__main__":
    main()

