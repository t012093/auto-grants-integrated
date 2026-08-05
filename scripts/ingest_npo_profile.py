#!/usr/bin/env python3
"""
NPO Profile Vector Ingest Script (ingest_npo_profile.py)
Fetches npo_profiles from Neon DB, generates 1024-dim embeddings for
activity_tags, target_audience, and description using SentenceTransformer in batches,
and saves them to public.npo_knowledge_chunks using ON CONFLICT safely.
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
DEFAULT_MODEL_NAME = "BAAI/bge-m3"
BATCH_COMMIT_SIZE = 10




class NPOProfileEmbedder:
    """Handles text formatting, batch embedding generation, and DB upsert for NPO profiles."""

    def __init__(self, db_url: str, model_name: str = DEFAULT_MODEL_NAME):
        self.db_url = db_url
        self.model_name = model_name
        logging.info(f"Loading embedding model: {model_name}...")
        self.model = SentenceTransformer(model_name)
        logging.info("Model loaded successfully.")

    def _fetch_past_awards(self, npo: Dict[str, Any], cur: Any) -> List[Dict[str, Any]]:
        """grant_past_awards から自団体の受賞実績を取得 (recipient_name 一致 or is_own_achievement)."""
        name = npo.get("name") or ""
        try:
            cur.execute(
                "SELECT funder_name, program_name, award_year, project_title, project_summary, "
                "evaluation_comment, award_amount, source_url, is_own_achievement "
                "FROM public.grant_past_awards "
                "WHERE is_own_achievement = TRUE ORDER BY award_year;"
            )
            rows = cur.fetchall()
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                return rows
            # dict_row でない環境向けに名前で再取得
            cur.execute(
                "SELECT funder_name, program_name, award_year, project_title, project_summary, "
                "evaluation_comment, award_amount, source_url "
                "FROM public.grant_past_awards WHERE recipient_name ILIKE %s ORDER BY award_year;",
                (f"%{name}%",),
            )
            return cur.fetchall() or []
        except Exception as e:
            logging.warning(f"grant_past_awards fetch failed: {e}")
            return []

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

        # 3. DESCRIPTION (+ track_records を統合した完全な団体概要)
        desc = npo.get("description") or ""
        trs = npo.get("track_records") or []
        if desc.strip():
            desc_text = f"団体概要・事業目的: {desc.strip()}"
            if isinstance(trs, list):
                for tr in trs:
                    if isinstance(tr, dict) and tr.get("summary"):
                        desc_text += f"\n実績サマリー: {tr['summary']}"
            chunks.append(("DESCRIPTION", desc_text))

        # 4. PAST_AWARD_{n}: 過去の助成金受賞実績 (grant_past_awards)
        for i, a in enumerate(self._fetch_past_awards(npo, cur), start=1):
            funder = a.get("funder_name") if isinstance(a, dict) else None
            program = a.get("program_name") if isinstance(a, dict) else None
            year = a.get("award_year") if isinstance(a, dict) else None
            title = a.get("project_title") if isinstance(a, dict) else None
            summary = a.get("project_summary") if isinstance(a, dict) else None
            eval_c = a.get("evaluation_comment") if isinstance(a, dict) else None
            parts = [f"過去の助成金受賞実績({year}年): {funder}{('/'+program) if program else ''}"]
            if title:
                parts.append(f"事業: {title}")
            if summary:
                parts.append(f"概要: {summary}")
            if eval_c:
                parts.append(f"評価: {eval_c}")
            chunks.append((f"PAST_AWARD_{i}", " ".join(parts)))

        if not chunks:
            logging.warning(f"No valid text fields found for NPO ID: {npo_id}")
            return 0

        # Batch encode all chunk contents for this NPO (high throughput)
        chunk_types = [c[0] for c in chunks]
        contents = [c[1] for c in chunks]
        embeddings = self.model.encode(contents, normalize_embeddings=True)

        saved_count = 0
        for chunk_type, content, vec in zip(chunk_types, contents, embeddings):
            # Upsert into npo_knowledge_chunks
            # Preserve original created_at on existing records
            cur.execute(
                """
                INSERT INTO public.npo_knowledge_chunks (npo_profile_id, chunk_type, content, embedding)
                VALUES (%s, %s, %s, %s::vector)
                ON CONFLICT (npo_profile_id, chunk_type)
                DO UPDATE SET
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding;
                """,
                (npo_id, chunk_type, content, str(vec.tolist()))
            )
            saved_count += 1
            logging.info(f"  └─ Saved {chunk_type} (length: {len(content)} chars)")

        return saved_count

    def run(self, org_id: Optional[str] = None, run_all: bool = False):
        if not org_id and not run_all:
            raise ValueError("Either --org-id or --all must be specified.")

        with psycopg.connect(self.db_url, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as select_cur, conn.cursor() as dml_cur:
                if org_id:
                    select_cur.execute("SELECT * FROM public.npo_profiles WHERE id = %s;", (org_id,))
                else:
                    select_cur.execute("SELECT * FROM public.npo_profiles ORDER BY created_at DESC;")

                processed_count = 0
                total_chunks = 0

                while True:
                    rows = select_cur.fetchmany(100)
                    if not rows:
                        break

                    for npo in rows:
                        total_chunks += self.process_npo(npo, dml_cur)
                        processed_count += 1

                        # Periodic commit per batch for transaction safety
                        if processed_count % BATCH_COMMIT_SIZE == 0:
                            conn.commit()
                            logging.debug(f"Committed transaction batch at {processed_count} NPOs.")

                conn.commit()

                if processed_count == 0:
                    logging.warning("No NPO profiles found to process.")
                    return

                logging.info(f"✨ Completed! Processed {processed_count} NPO profiles, saved {total_chunks} vector chunks.")



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
