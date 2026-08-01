#!/usr/bin/env python3
"""
Sync Proposal to ai-note-meet (sync_proposal_to_ai_note_meet.py)

auto-grants-integrated の grant_proposals テーブルから企画書データを取得し、
ai-note-meet の MCP ツール群を呼び出して以下を自動キックオフする：
  1. プロジェクト作成 (create_project)
  2. 企画書 Fumadocs Wiki ページ作成 (create_page: ペラ1サマリー + 詳細)
  3. カレンダーへ締め切り・事前相談日を登録 (create_calendar_entry)
  4. メンバー募集オファーのアナウンス投稿 (create_announcement)
  5. ポジション別初期タスクの自動アサイン (create_task)

Usage:
    uv run scripts/sync_proposal_to_ai_note_meet.py --proposal-id <UUID>
"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
import psycopg.rows
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")


# =============================================================================
# 1. DB からの企画書データ取得
# =============================================================================

def fetch_proposal(conn: psycopg.Connection, proposal_id: str) -> Optional[Dict[str, Any]]:
    """grant_proposals テーブルから企画書データを取得する。"""
    row = conn.execute(
        """
        SELECT id, npo_id, title, concept_summary, status,
               content_markdown, budget_json, kpi_json,
               ai_note_project_id, ai_note_page_id
        FROM public.grant_proposals
        WHERE id = %s
        """,
        (proposal_id,),
    ).fetchone()
    return dict(row) if row else None


def fetch_grant_mappings(conn: psycopg.Connection, proposal_id: str) -> List[Dict[str, Any]]:
    """proposal_grant_mappings から紐づけ助成金を取得する。"""
    rows = conn.execute(
        """
        SELECT pgm.grant_id, pgm.is_primary, pgm.match_score, pgm.status,
               g.title AS grant_title, g.subsidy_max_limit
        FROM public.proposal_grant_mappings pgm
        JOIN public.grants g ON g.id = pgm.grant_id
        WHERE pgm.proposal_id = %s
        ORDER BY pgm.is_primary DESC, pgm.match_score DESC NULLS LAST
        """,
        (proposal_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_offers(conn: psycopg.Connection, proposal_id: str) -> List[Dict[str, Any]]:
    """proposal_project_offers からポジション定義を取得する。"""
    rows = conn.execute(
        """
        SELECT id, position_code, position_name, capacity,
               task_allocation_tag, compensation_notes, initial_tasks_json, status
        FROM public.proposal_project_offers
        WHERE proposal_id = %s
        ORDER BY id
        """,
        (proposal_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# =============================================================================
# 2. ペラ1サマリー Markdown 自動生成
# =============================================================================

def generate_summary_page(proposal: Dict, grants: List[Dict], offers: List[Dict]) -> str:
    """30秒でわかるペラ1サマリーページの Markdown を生成する。"""
    primary_grant = next((g for g in grants if g.get("is_primary")), grants[0] if grants else None)
    grant_title = primary_grant["grant_title"] if primary_grant else "未定"
    match_score = primary_grant.get("match_score", "?") if primary_grant else "?"
    max_limit = primary_grant.get("subsidy_max_limit") if primary_grant else None
    amount_str = f"{int(max_limit):,}円" if max_limit else "未定"

    budget = proposal.get("budget_json") or {}
    budget_lines = ""
    if isinstance(budget, dict) and budget:
        for category, amount in budget.items():
            if isinstance(amount, (int, float)):
                budget_lines += f"- **{category}**: {int(amount):,}円\n"

    positions_table = ""
    for o in offers:
        status_mark = "確定" if o["status"] == "FILLED" else "募集中"
        positions_table += (
            f"| {o['position_name']} | {o['capacity']}名 | {status_mark} |\n"
        )

    md = f"""# ⚡ 30秒でわかる！{proposal['title']} (ペラ1)

> **対象助成金**: {grant_title}
> **申請予定金額**: {amount_str} | **AI適合スコア**: {match_score}%

---

## 🎯 コンセプト

{proposal.get('concept_summary') or '（未記入）'}

---

## 💰 予算計画イメージ

{budget_lines or '（未策定）'}

---

## 👥 募集中のポジション

| ポジション | 人数 | 状態 |
|---|---|---|
{positions_table or '| （未設定） | - | - |'}

---

👉 **詳細企画書は次のページをご覧ください**
"""
    return md


# =============================================================================
# 3. ai-note-meet MCP 連携ハンドラー
# =============================================================================

class AiNoteMeetSyncer:
    """
    ai-note-meet MCP ツールの呼び出しを行うハンドラークラス。

    NOTE: 実際の MCP 呼び出しは Antigravity Agent 経由で行われるため、
    このクラスは「どのツールを、どの順番で、どのデータで呼ぶか」を
    構造化して出力する役割を担う（ドライラン対応）。
    """

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.execution_log: List[Dict[str, Any]] = []

    def _call(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """MCP ツール呼び出しを記録する。"""
        entry = {"tool": tool_name, "args": args}
        self.execution_log.append(entry)

        if self.dry_run:
            logger.info(f"[DRY RUN] {tool_name}: {json.dumps(args, ensure_ascii=False, indent=2)[:200]}")
            return {"status": "dry_run", "tool": tool_name}

        # NOTE: 実際のMCPは Agent が call_mcp_tool で実行するため、
        # ここではプランのみ出力する。
        logger.info(f"[PLAN] {tool_name}: {json.dumps(args, ensure_ascii=False)[:200]}")
        return {"status": "planned", "tool": tool_name}

    def sync(self, proposal: Dict, grants: List[Dict], offers: List[Dict]) -> List[Dict]:
        """
        企画書データを ai-note-meet へ同期する全ステップを実行する。
        Returns: 実行ログのリスト
        """
        project_name = proposal["title"]

        # Step 1: プロジェクト作成
        self._call("create_project", {
            "name": project_name,
            "description": proposal.get("concept_summary", ""),
        })

        # Step 2: ペラ1 Wiki ページ作成
        summary_md = generate_summary_page(proposal, grants, offers)
        self._call("create_page", {
            "title": f"🏠 プロジェクト概要 (ペラ1): {project_name}",
            "content": summary_md,
        })

        # Step 3: 詳細企画書ページ作成
        if proposal.get("content_markdown"):
            self._call("create_page", {
                "title": f"📖 詳細企画書: {project_name}",
                "content": proposal["content_markdown"],
            })

        # Step 4: カレンダー登録 (本命助成金の締切)
        primary_grant = next((g for g in grants if g.get("is_primary")), None)
        if primary_grant:
            self._call("create_calendar_entry", {
                "title": f"【締切】{primary_grant['grant_title']}",
                "description": f"助成金公募の最終締切日",
            })

        # Step 5: アナウンス（オファー募集）
        if offers:
            positions_text = "\n".join(
                f"- {o['position_name']} ({o['capacity']}名募集)"
                for o in offers if o["status"] == "RECRUITING"
            )
            if positions_text:
                self._call("create_announcement", {
                    "title": f"【新プロジェクト発足】{project_name} メンバー募集！",
                    "content": f"以下のポジションでメンバーを先着順で募集します。\n\n{positions_text}",
                })

        # Step 6: ポジション別タスクの自動生成
        for offer in offers:
            tasks = offer.get("initial_tasks_json") or []
            if isinstance(tasks, str):
                try:
                    tasks = json.loads(tasks)
                except json.JSONDecodeError:
                    tasks = []
            for task_title in tasks:
                self._call("create_task", {
                    "title": task_title,
                    "description": f"ポジション: {offer['position_name']}",
                })

        return self.execution_log


# =============================================================================
# 4. DB への ai-note-meet 連携ID書き戻し
# =============================================================================

def update_proposal_ai_note_ids(
    conn: psycopg.Connection,
    proposal_id: str,
    project_id: str,
    page_id: str,
) -> None:
    """ai-note-meet のプロジェクトID・ページIDを grant_proposals に書き戻す。"""
    conn.execute(
        """
        UPDATE public.grant_proposals
        SET ai_note_project_id = %s, ai_note_page_id = %s, updated_at = NOW()
        WHERE id = %s
        """,
        (project_id, page_id, proposal_id),
    )
    conn.commit()
    logger.info(f"Updated proposal {proposal_id}: project_id={project_id}, page_id={page_id}")


# =============================================================================
# 5. CLI エントリーポイント
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="企画書データを ai-note-meet へ自動同期するスクリプト"
    )
    parser.add_argument(
        "--proposal-id", required=True,
        help="同期する grant_proposals の UUID"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="実際のMCPは呼ばず、実行プランのみ出力する (デフォルト: True)"
    )
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL が .env に設定されていません")
        sys.exit(1)

    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        # データ取得
        proposal = fetch_proposal(conn, args.proposal_id)
        if not proposal:
            logger.error(f"Proposal not found: {args.proposal_id}")
            sys.exit(1)

        grants = fetch_grant_mappings(conn, args.proposal_id)
        offers = fetch_offers(conn, args.proposal_id)

        logger.info(f"Proposal: {proposal['title']} (status={proposal['status']})")
        logger.info(f"  Mapped grants: {len(grants)}")
        logger.info(f"  Positions: {len(offers)}")

        # 同期実行
        syncer = AiNoteMeetSyncer(dry_run=args.dry_run)
        log = syncer.sync(proposal, grants, offers)

        # 実行プランを出力
        print("\n" + "=" * 60)
        print("ai-note-meet 同期実行プラン")
        print("=" * 60)
        for i, entry in enumerate(log, 1):
            print(f"\n[Step {i}] {entry['tool']}")
            print(f"  Args: {json.dumps(entry['args'], ensure_ascii=False, indent=4)[:500]}")

        print(f"\n合計 {len(log)} ステップの MCP 呼び出しが計画されました。")
        if args.dry_run:
            print("(--dry-run モード: 実際のMCP呼び出しは行われていません)")


if __name__ == "__main__":
    main()
