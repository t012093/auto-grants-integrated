#!/usr/bin/env python3
"""
task_human_ai_allocator.py — 人間×AI タスク自動分離・ポジション設計 (Phase 1: ルール主軸)

助成金企画書 (grant_proposals.content_markdown / budget_json) から、
事業を遂行するタスク群を AUTO/HYBRID/HUMAN に分類し、人間が必要な
ポジションを設計して proposal_project_offers に書き込む。

spec: skills/task_human_ai_allocator/spec.md
  - 決定者設計: L1共通/システム・L2ポジション/システム・L3固有/ルール+人間承認・L4タグ/ルール
  - 出力先: proposal_project_offers (現行スキーマ・スキーマ変更なし)
  - ai-note-meet 実同期は sync_proposal_to_ai_note_meet.py に委譲(二重実装しない)

Usage:
    env -u PYTHONPATH uv run scripts/task_human_ai_allocator.py \
        --proposal-id <UUID> [--json] [--reset]

検証 E2E:
    env -u PYTHONPATH uv run scripts/task_human_ai_allocator.py \
        --proposal-id <UUID> --json
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
import psycopg.rows
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

_SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "task_human_ai_allocator"

VALID_TAGS = ("AUTO", "HYBRID", "HUMAN")


def load_json(name: str) -> Dict[str, Any]:
    with open(_SKILL_DIR / name, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1. 事業型判定 (spec §4: キーワード→型、判定不能は GENERIC にフォールバック)
# ---------------------------------------------------------------------------
def detect_business_type(content: str, templates: Dict[str, Any]) -> str:
    text = (content or "").lower()
    for code, tpl in templates["business_types"].items():
        if code == templates["fallback_type"]:
            continue
        if any(k.lower() in text for k in tpl.get("keywords", [])):
            return code
    return templates["fallback_type"]


# ---------------------------------------------------------------------------
# 2. タグ判定 (spec §6: HUMAN優先→AUTO→HYBRID→フォールバックHYBRID)
# ---------------------------------------------------------------------------
def _match_tag(title: str, rules: Dict[str, Any], tag: str) -> bool:
    text = (title or "").lower()
    return any(k.lower() in text for k in rules["rules"].get(tag, []))


def assign_tag(title: str, tag_rules: Dict[str, Any], preset: Optional[str] = None) -> str:
    """タグを判定する。preset(テンプレート指定)があればそれを優先。"""
    if preset and preset in VALID_TAGS:
        return preset
    for tag in tag_rules["priority"]:  # HUMAN → AUTO → HYBRID
        if _match_tag(title, tag_rules, tag):
            return tag
    return tag_rules["fallback_tag"]  # 不明は HYBRID(人間承認を挟む=安全側)


# ---------------------------------------------------------------------------
# 3. ポジション設計 (spec §7) & タスク組み立て
# ---------------------------------------------------------------------------
def _build_offer(proposal_id: str, proposal: Dict[str, Any], templates: Dict[str, Any]) -> str:
    """事業型テンプレートから tasks / positions を構築する。"""
    content = proposal.get("content_markdown") or ""
    btype = detect_business_type(content, templates)
    tpl = templates["business_types"][btype]

    positions = []
    for p in tpl.get("positions", []):
        positions.append({
            "position_code": p["position_code"],
            "position_name": p["position_name"],
            "capacity": p["capacity"],
            "task_allocation_tag": p["tag"],
        })

    # タスク = 共通タスク(PM/HYBRID) + 事業型固有タスク
    tasks = []
    for t in templates.get("common_tasks", []):
        tasks.append({
            "title": t["title"],
            "tag": t["tag"],
            "assigned_position": t["position"],
        })
    for t in tpl.get("tasks", []):
        tasks.append({
            "title": t["title"],
            "tag": t["tag"],
            "assigned_position": t["position"],
        })

    project_name = proposal.get("title") or "プロジェクト"
    return json.dumps({
        "proposal_id": proposal_id,
        "project_name": project_name,
        "business_type": btype,
        "tasks": tasks,
        "positions": positions,
        "offer_announcement_text": f"【新プロジェクト発足】{project_name} メンバー募集！",
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 4. DB 永続化 (spec §8: proposal_project_offers へ書き込み・冪等)
# ---------------------------------------------------------------------------
def persist_offers(conn, proposal_id: str, allocation: Dict[str, Any], reset: bool = False) -> int:
    """proposal_project_offers へポジションを書き込む (spec §8.2 冪等 Upsert)。

    UNIQUE 制約が無いため ON CONFLICT は使わず、既存行(position_code単位)を
    手動 Upsert(存在→UPDATE / 不在→INSERT)する。reset 時は一括削除からの再生成。
    """
    written = 0
    with conn.cursor() as cur:
        if reset:
            cur.execute("DELETE FROM public.proposal_project_offers WHERE proposal_id = %s", (proposal_id,))
            conn.commit()
        for pos in allocation["positions"]:
            initial_tasks = [
                {"title": t["title"], "tag": t["tag"]}
                for t in allocation["tasks"] if t["assigned_position"] == pos["position_code"]
            ]
            tasks_json = json.dumps(initial_tasks, ensure_ascii=False)
            cur.execute(
                "SELECT id FROM public.proposal_project_offers "
                "WHERE proposal_id = %s AND position_code = %s",
                (proposal_id, pos["position_code"]),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """
                    UPDATE public.proposal_project_offers SET
                      position_name = %s, capacity = %s, task_allocation_tag = %s,
                      compensation_notes = %s, initial_tasks_json = %s, status = 'RECRUITING'
                    WHERE id = %s
                    """,
                    (pos["position_name"], pos["capacity"], pos["task_allocation_tag"],
                     None, tasks_json, existing["id"]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO public.proposal_project_offers
                      (proposal_id, position_code, position_name, capacity,
                       task_allocation_tag, compensation_notes, initial_tasks_json, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'RECRUITING')
                    """,
                    (proposal_id, pos["position_code"], pos["position_name"], pos["capacity"],
                     pos["task_allocation_tag"], None, tasks_json),
                )
            written += 1
        conn.commit()
    return written


# ---------------------------------------------------------------------------
# 5. 実行フロー
# ---------------------------------------------------------------------------
def run(proposal_id: str, db_url: Optional[str] = None, reset: bool = False,
        tags_json: Optional[str] = None, templates_json: Optional[str] = None) -> Dict[str, Any]:
    """gr.
    企画書を読み、タスク/ポジションを設計して proposal_project_offers に書き込む。
    db_url が None のときは書き込みをせず設計結果のみ返す(テスト/ドライラン用)。
    """
    tag_rules = json.loads(tags_json) if tags_json else load_json("tag_rules.json")
    templates = json.loads(templates_json) if templates_json else load_json("business_templates.json")

    if not db_url:
        proposal = {
            "id": proposal_id,
            "title": "サンプル企画書",
            "content_markdown": "(テスト用: キーワードなし)",
            "budget_json": {},
        }
        return json.loads(_build_offer(proposal_id, proposal, templates))

    with psycopg.connect(db_url, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.grant_proposals WHERE id = %s",
                (proposal_id,),
            )
            proposal = cur.fetchone()
            if not proposal:
                raise ValueError(f"Proposal with ID '{proposal_id}' not found in DB.")

        allocation = json.loads(_build_offer(proposal_id, proposal, templates))
        persist_offers(conn, proposal_id, allocation, reset=reset)
        return allocation


def main():
    parser = argparse.ArgumentParser(description="助成金企画書からタスク/ポジションを設計 (Phase 1: ルール主軸)")
    parser.add_argument("--proposal-id", required=True, help="grant_proposals の UUID")
    parser.add_argument("--reset", action="store_true", help="既存オファーを削除して再生成")
    parser.add_argument("--json", action="store_true", help="設計結果を JSON 出力")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("❌ Error: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    try:
        res = run(args.proposal_id, db_url=DATABASE_URL, reset=args.reset)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"✅ ポジション設計完了: proposal={args.proposal_id} 事業型={res['business_type']}")
        for pos in res["positions"]:
            print(f"  - {pos['position_code']} ({pos['position_name']}): {pos['capacity']}名")


if __name__ == "__main__":
    main()
