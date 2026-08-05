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
               g.title AS grant_title, g.amount_max AS subsidy_max_limit
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
        self.plan: List[Dict[str, Any]] = []

    def _add_step(self, tool_name: str, args: Dict[str, Any], kind: str = "mcp", **extra) -> Dict[str, Any]:
        """MCP 呼び出し(または実行後DB書き戻し)の計画ステップを記録する。

        本スクリプトは実際の MCP 呼び出しを行わず、実行順序・引数を構造化した
        「計画」だけを持つ。実実行は Agent(Antigravity/Hermes 等)が mcp__ai_note_meet__*
        を呼び、終了後に本計画の write_back ステップへ従って DB を書き戻す。
        """
        entry: Dict[str, Any] = {
            "step": len(self.plan) + 1,
            "kind": kind,
            "tool": tool_name,
            "args": args,
        }
        entry.update(extra)
        self.plan.append(entry)
        logger.info("[PLAN] %s: %s", tool_name, json.dumps(args, ensure_ascii=False)[:200])
        return entry

    def _call(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """後方互換: MCP 計画ステップを追加する。"""
        return self._add_step(tool_name, args)

    def sync(self, proposal: Dict, grants: List[Dict], offers: List[Dict]) -> List[Dict]:
        """
        企画書データを ai-note-meet へ同期する MCP 呼び出し計画を構築する。
        Returns: 実行計画ステップのリスト (各要素は dict, 必ず 'tool' キーを持つ)
        """
        # 冪等性: ai_note_project_id が設定済み(= 既に同期済み)なら再生成しない
        if proposal.get("ai_note_project_id"):
            entry = self._add_step(
                "__already_synced",
                {"proposal_id": proposal.get("id")},
                kind="info",
                note="ai_note_project_id が設定済みのため同期をスキップ(冪等)",
            )
            return [entry]

        project_name = proposal["title"]

        # Step 1: プロジェクト作成
        self._add_step("create_project", {
            "name": project_name,
            "description": proposal.get("concept_summary", ""),
        })

        # Step 2: ペラ1 Wiki ページ作成
        summary_md = generate_summary_page(proposal, grants, offers)
        self._add_step("create_page", {
            "title": f"🏠 プロジェクト概要 (ペラ1): {project_name}",
            "content": summary_md,
        })

        # Step 3: 詳細企画書ページ作成
        if proposal.get("content_markdown"):
            self._add_step("create_page", {
                "title": f"📖 詳細企画書: {project_name}",
                "content": proposal["content_markdown"],
            })

        # Step 4: カレンダー登録 (本命助成金の締切)
        primary_grant = next((g for g in grants if g.get("is_primary")), None)
        if primary_grant:
            self._add_step("create_calendar_entry", {
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
                self._add_step("create_announcement", {
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
                self._add_step("create_task", {
                    "title": task_title,
                    "description": f"ポジション: {offer['position_name']}",
                })

        # Step 7(実行後): MCP で取得した ai-note-meet 連携IDを grant_proposals へ書き戻す
        #   (実行者=Agent が create_project / create_page の返り値IDで実行する)
        self._add_step(
            "update_proposal_ai_note_ids",
            {
                "proposal_id": proposal.get("id"),
                "ai_note_project_id": "<create_projectの返り値>",
                "ai_note_page_id": "<create_pageの返り値>",
            },
            kind="write_back",
            when="create_project / create_page が成功し ID を取得できた後に実行",
            note="grant_proposals の ai_note_project_id / ai_note_page_id を更新して冪等化する",
        )

        return self.plan


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
        "--dry-run", action="store_true", default=False,
        help="計画のみ表示(本スクリプトは元々計画生成のみ。既定でも実MCP呼び出しは行わない)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="MCP 実行計画を JSON で stdout に出力(Agent が消費する機械可読形式)"
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

        # 同期計画を構築
        syncer = AiNoteMeetSyncer(dry_run=args.dry_run)
        plan = syncer.sync(proposal, grants, offers)

    # 出力
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    mode_label = f"({len(plan)}ステップ)"
    print("\n" + "=" * 60)
    print(f"ai-note-meet 同期 実行計画 {mode_label}")
    print("=" * 60)
    for entry in plan:
        kind = entry.get("kind", "mcp")
        print(f"\n[Step {entry['step']}] ({kind}) {entry['tool']}")
        if entry.get("when"):
            print(f"  When: {entry['when']}")
        if entry.get("note"):
            print(f"  Note: {entry['note']}")
        print(f"  Args: {json.dumps(entry['args'], ensure_ascii=False, indent=4)[:500]}")

    print(f"\n合計 {len(plan)} ステップの計画を生成しました。")
    print(
        "このスクリプトは MCP 呼び出しの**計画生成**のみを行います。"
        "実際の ai-note-meet 同期は Agent が mcp__ai_note_meet__* ツールで実行し、"
        "最後の write_back ステップ(update_proposal_ai_note_ids)で連携IDを DB へ書き戻してください。"
    )


if __name__ == "__main__":
    main()
