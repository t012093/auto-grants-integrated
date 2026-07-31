#!/usr/bin/env python3
"""
NPO Profile Vector Ingest Script (ingest_npo_profile.py)
Fetches npo_profiles from Neon DB, generates 768-dim embeddings for
activity_tags, target_audience, and description using SentenceTransformer,
and saves them to public.npo_knowledge_chunks using ON CONFLICT.
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
import psycopg
import psycopg.rows
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
DEFAULT_MODEL_NAME = "cl-tohoku/bert-base-japanese-v3"



class NPOProfileEmbedder:
    """Handles text formatting, embedding generation, and DB upsert for NPO profiles."""

    def __init__(self, db_url: str, model_name: str = DEFAULT_MODEL_NAME):
        self.db_url = db_url
        self.model_name = model_name
        logging.info(f"Loading embedding model: {model_name}...")
        self.model = SentenceTransformer(model_name)
        logging.info("Model loaded successfully.")

    def process_npo(self, npo: Dict[str, Any], cur: Any) -> int:
        npo_id = str(npo["id"])
        npo_name = npo.get("name", "名称未設定")
        logging.info(f"Processing NPO Profile: {npo_name} (ID: {npo_id})")

        chunks = []

        # 1. ACTIVITY_TAGS
        tags = npo.get("activity_tags") or []
        if tags:
            tag_text = f"活動分野・主要テーマ: {', '.join(tags)}"
            chunks.append(("ACTIVITY_TAGS", tag_text))

        # 2. TARGET_AUDIENCE
        audiences = npo.get("target_audience") or []
        if audiences:
            aud_text = f"支援対象・ターゲット層: {', '.join(audiences)}"
            chunks.append(("TARGET_AUDIENCE", aud_text))

        # 3. DESCRIPTION
        desc = npo.get("description") or ""
        if desc.strip():
            desc_text = f"団体概要・事業目的: {desc.strip()}"
            chunks.append(("DESCRIPTION", desc_text))

        if not chunks:
            logging.warning(f"No valid text fields found for NPO ID: {npo_id}")
            return 0

        saved_count = 0
        for chunk_type, content in chunks:
            # Generate 768-dim vector
            vec = self.model.encode(content).tolist()

            # Upsert into npo_knowledge_chunks
            cur.execute(
                """
                INSERT INTO public.npo_knowledge_chunks (npo_profile_id, chunk_type, content, embedding)
                VALUES (%s, %s, %s, %s::vector)
                ON CONFLICT (npo_profile_id, chunk_type)
                DO UPDATE SET
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding,
                    created_at = NOW();
                """,
                (npo_id, chunk_type, content, str(vec))
            )
            saved_count += 1
            logging.info(f"  └─ Saved {chunk_type} (length: {len(content)} chars)")

        return saved_count

    def run(self, org_id: Optional[str] = None, run_all: bool = False):
        if not org_id and not run_all:
            raise ValueError("Either --org-id or --all must be specified.")

        with psycopg.connect(self.db_url, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                if org_id:
                    cur.execute("SELECT * FROM public.npo_profiles WHERE id = %s;", (org_id,))
                    npos = cur.fetchall()
                else:
                    cur.execute("SELECT * FROM public.npo_profiles ORDER BY created_at DESC;")
                    npos = cur.fetchall()

                if not npos:
                    logging.warning("No NPO profiles found to process.")
                    return

                total_chunks = 0
                for npo in npos:
                    total_chunks += self.process_npo(npo, cur)

            conn.commit()
            logging.info(f"✨ Completed! Processed {len(npos)} NPO profiles, saved {total_chunks} vector chunks.")


def main():
    parser = argparse.ArgumentParser(description="NPO Profile Vector Ingest Script")
    parser.add_argument("--org-id", help="NPO Profile UUID")
    parser.add_argument("--all", action="store_true", help="Process all NPO profiles in DB")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME, help="SentenceTransformer model name")

    args = parser.parse_args()

    if not DATABASE_URL:
        logging.error("DATABASE_URL is not set in environment variables.")
        sys.exit(1)

    try:
        embedder = NPOProfileEmbedder(DATABASE_URL, model_name=args.model_name)
        embedder.run(org_id=args.org_id, run_all=args.all)
    except Exception as e:
        logging.error(f"Execution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
