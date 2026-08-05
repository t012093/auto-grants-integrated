#!/usr/bin/env python3
"""
telegram_bridge.py — Telegram 助成金ブリッジ
=============================================

ハーネス(DB) と Telegram Bot を橋渡しする薄いレイヤー。
* 下り: public.alerts(is_notified=False) の新着適合 + 締切接近を Telegram へインラインボタン付きで送信 → is_notified 更新
* 上り: ボタン(コールバック)を受け、既存スキル/スクリプトへ委譲
  - 企画書を起稿 → grant_form_filler.generate_proposal_docx.py
  - 検討中に追加 → proposal_grant_mappings へ CONSIDERING 登録
  - 対象外 → alert を is_read 化

設計方針: Telegram は「リマインド+承認」の薄い窓口。中身(書類生成等)は既存の
CLI スキルに委譲し、UI にロジックを持たせない。

設定 (.env):
  TELEGRAM_BOT_TOKEN = BotFather で発行
  TELEGRAM_CHAT_ID   = 通知先チャット/グループ ID (先頭 -100 等)

usage:
  # 未通知アラートをスキャンして送信 (is_notified 更新)
  env -u PYTHONPATH uv run skills/telegram_grant_bridge/scripts/telegram_bridge.py notify
  env -u PYTHONPATH uv run skills/telegram_grant_bridge/scripts/telegram_bridge.py notify --dry-run

  # インラインボタン(コールバック)を long-polling で受付
  env -u PYTHONPATH uv run skills/telegram_grant_bridge/scripts/telegram_bridge.py poll
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("telegram_bridge")


# ---------------------------------------------------------------------------
# 設定チェック
# ---------------------------------------------------------------------------
def check_config(require_send: bool = True) -> None:
    if not DATABASE_URL:
        logger.error("DATABASE_URL が未設定です")
        sys.exit(1)
    if require_send and (not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID):
        logger.error("TELEGRAM_BOT_TOKEN または TELEGRAM_CHAT_ID が未設定 (.env) です")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Telegram 送信
# ---------------------------------------------------------------------------
def send_message(text: str, inline_keyboard: Optional[List[List[Dict]]] = None,
                 chat_id: Optional[str] = None) -> Dict[str, Any]:
    """Send a message with optional inline keyboard. Returns Bot API JSON."""
    url = f"{API_BASE}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if inline_keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard})
    r = httpx.post(url, json=payload, timeout=20.0)
    return r.json()


# ---------------------------------------------------------------------------
# メッセージ & ボタン構築
# ---------------------------------------------------------------------------
def is_closing(deadline: Optional[str]) -> Optional[str]:
    """deadline が X 日以内ならラベルを返す。"""
    if not deadline:
        return None
    try:
        d = datetime.strptime(str(deadline)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    days = (d - date.today()).days
    if days < 0:
        return "⚠️ 締切超過"
    if days <= 7:
        return f"⏰ 締切まで{days}日"
    if days <= 14:
        return f"📅 締切まで{days}日"
    return None


def build_inline_keyboard(grant_id: int, details_url: str) -> List[List[Dict]]:
    return [
        [
            {"text": "✍️ 企画書を起稿", "callback_data": f"proposal:{grant_id}"},
            {"text": "📋 書類を見る", "callback_data": f"doc:{grant_id}"},
        ],
        [
            {"text": "🔗 公式ページ", "url": details_url or f"https://www.jgrants-portal.go.jp/subsidy/{grant_id}"},
            {"text": "✅ 検討中に追加", "callback_data": f"consider:{grant_id}"},
            {"text": "❌ 対象外", "callback_data": f"dismiss:{grant_id}"},
        ],
    ]


def build_alert_message(title: str, match_score: int, overall_status: str, deadline: Optional[str],
                        target_area: Optional[str], is_rate_10_10: bool, details_url: str) -> str:
    status_icon = {"ELIGIBLE": "✅", "CONDITIONAL": "⚠️", "INELIGIBLE": "❌"}.get(overall_status, "❓")
    closing = is_closing(deadline)
    rate = "10/10全額" if is_rate_10_10 else ""
    area = target_area or "全国"
    lines = [
        f"{status_icon} <b>{title}</b>",
        f"適合スコア: {match_score}% ({overall_status})",
    ]
    detail = " | ".join(x for x in [f"締切: {deadline or '未設定'}", f"対象: {area}", rate] if x)
    if detail:
        lines.append(detail)
    if closing:
        lines.append(closing)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 下り: 未通知アラートのスキャン & 送信
# ---------------------------------------------------------------------------
def scan_and_notify(dry_run: bool = False, min_score: int = 60) -> int:
    import psycopg
    import psycopg.rows

    sent = 0
    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        rows = conn.execute(
            """
            SELECT a.id AS alert_id, a.title, a.match_score, a.overall_status,
                   g.id AS grant_id, g.deadline, g.target_area, g.is_rate_10_10, g.details_url
            FROM public.alerts a
            JOIN public.grants g ON g.id = a.grant_id
            WHERE a.is_notified = FALSE
              AND a.overall_status IN ('ELIGIBLE', 'CONDITIONAL')
              AND COALESCE((a.report_json->>'coverage')::float, 0) >= 0.6
            ORDER BY a.match_score DESC, a.created_at DESC
            LIMIT 50;
            """
        ).fetchall()

        if not rows:
            logger.info("未通知アラートなし")
            return 0

        # ノイズ抑止: 適合スコアが下限未満 かつ 締切接近でもないものは送らない
        target_rows = []
        for row in rows:
            score = row["match_score"] or 0
            closing = is_closing(row["deadline"])
            if score >= min_score or closing is not None:
                target_rows.append(row)

        if not target_rows:
            logger.info("送信対象なし (min_score=%d 未満 & 締切接近なし)", min_score)
            return 0

        for row in target_rows:
            text = build_alert_message(
                row["title"], row["match_score"] or 0, row["overall_status"] or "UNKNOWN",
                row["deadline"], row["target_area"], row["is_rate_10_10"] or False, row["details_url"] or "",
            )
            kb = build_inline_keyboard(row["grant_id"], row["details_url"] or "")
            if dry_run:
                logger.info("[DRY] 送信予定: %s\n%s", row["title"], text)
                logger.info("[DRY] ボタン: %s", [b.get("callback_data") or b.get("url") for row_b in kb for b in row_b])
                continue

            resp = send_message(text, kb)
            if not resp.get("ok"):
                logger.warning("送信失敗 alert_id=%s: %s", row["alert_id"], resp.get("description"))
                continue
            conn.execute(
                "UPDATE public.alerts SET is_notified = TRUE, is_read = TRUE WHERE id = %s;",
                (row["alert_id"],),
            )
            sent += 1

        conn.commit()
        logger.info("送信/既読化: %d 件", sent if not dry_run else len(target_rows))
        return sent if not dry_run else len(target_rows)


# ---------------------------------------------------------------------------
# 上り: コールバック処理
# ---------------------------------------------------------------------------
def run_generate_proposal(grant_id: str) -> str:
    """企画書起稿スクリプトを呼び出す (DB に NPO id が必要)。"""
    script = Path(__file__).resolve().parent.parent.parent / "grant_form_filler" / "scripts" / "generate_proposal_docx.py"
    if not script.exists():
        return f"⚠️ generate_proposal_docx.py が見つかりません"
    try:
        # org-id は DB の先頭 NPO を使用 (将来は proposal から解決)
        import psycopg
        import psycopg.rows
        with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
            npo = conn.execute("SELECT id FROM public.npo_profiles ORDER BY created_at LIMIT 1;").fetchone()
        org_id = str(npo["id"]) if npo else None
        if not org_id:
            return "⚠️ 団体プロファイル未登録"
        r = subprocess.run(
            [sys.executable, str(script), "--org-id", org_id, "--grant-id", grant_id, "--markdown-only"],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0 or "Error" in (r.stderr or ""):
            return f"⚠️ 企画書起稿失敗: {(r.stderr or r.stdout)[-200:]}"
        return f"✅ 企画書を起稿しました (grant {grant_id})"
    except Exception as e:
        return f"⚠️ 企画書起稿エラー: {e}"


def add_to_considering(grant_id: str) -> str:
    import psycopg
    import psycopg.rows
    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        # 既存 proposal (先頭) に grant を紐付け、無ければ grant_proposals を新規作成
        prop = conn.execute("SELECT id FROM public.grant_proposals ORDER BY created_at LIMIT 1;").fetchone()
        if prop:
            conn.execute(
                """INSERT INTO public.proposal_grant_mappings (proposal_id, grant_id, status)
                   VALUES (%s, %s, 'CONSIDERING')
                   ON CONFLICT (proposal_id, grant_id) DO UPDATE SET status='CONSIDERING';""",
                (str(prop["id"]), int(grant_id)),
            )
            conn.commit()
            return f"✅ 検討中リストに追加しました (grant {grant_id})"
        return "⚠️ 企画書プロジェクトが未作成です"
    return "⚠️ DB接続エラー"


def handle_callback(query: Dict[str, Any]) -> str:
    data = (query.get("data") or "").strip()
    action, _, grant_id = data.partition(":")
    reply = ""
    if action == "proposal":
        reply = run_generate_proposal(grant_id)
    elif action == "doc":
        import psycopg
        import psycopg.rows
        with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
            g = conn.execute(
                "SELECT title, required_documents, details_url FROM public.grants WHERE id = %s;", (int(grant_id),)
            ).fetchone()
        reply = (f"📋 {g['title']}\n必要書類: {', '.join(g['required_documents']) if g['required_documents'] else '未設定'}\nURL: {g['details_url'] or 'なし'}"
                 if g else "書類情報なし")
    elif action == "consider":
        reply = add_to_considering(grant_id)
    elif action == "dismiss":
        import psycopg
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("UPDATE public.alerts SET is_read = TRUE WHERE grant_id = %s;", (int(grant_id),))
            conn.commit()
        reply = "この助成金を対象外として既読化しました"
    else:
        reply = f"未対応アクション: {action}"
    return reply


def poll_callbacks() -> None:
    """getUpdates を long-polling し、callback_query を処理する。"""
    offset: Optional[int] = None
    url = f"{API_BASE}/getUpdates"
    while True:
        params = {"timeout": 50}
        if offset:
            params["offset"] = offset
        try:
            resp = httpx.get(url, params=params, timeout=60.0).json()
        except Exception as e:
            logger.warning("getUpdates error: %s", e)
            time.sleep(5)
            continue
        if not resp.get("ok"):
            logger.warning("getUpdates not ok: %s", resp.get("description"))
            time.sleep(5)
            continue
        for update in resp.get("result", []):
            offset = update["update_id"] + 1
            cq = update.get("callback_query")
            if not cq:
                continue
            try:
                reply = handle_callback(cq)
            except Exception as e:
                reply = f"⚠️ 処理エラー: {e}"
            # ボタンの「処理中」を消して結果を返す
            answer = f"{API_BASE}/answerCallbackQuery"
            httpx.post(answer, json={"callback_query_id": cq["id"], "text": reply, "show_alert": False}, timeout=20.0)
            # 元チャットへ結果を通知
            msg = cq.get("message", {})
            chat = msg.get("chat", {}).get("id")
            if chat:
                send_message(reply, chat_id=str(chat))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram 助成金ブリッジ")
    sub = parser.add_subparsers(dest="command", required=True)

    p_notify = sub.add_parser("notify", help="未通知アラートをスキャンして Telegram 送信")
    p_notify.add_argument("--dry-run", action="store_true")
    p_notify.add_argument("--min-score", type=int, default=60,
                          help="送信する適合スコア下限 (締切接近のものは常に送信)")

    sub.add_parser("poll", help="インラインボタン(コールバック)を long-polling 受付")

    args = parser.parse_args()

    if args.command == "notify":
        check_config(require_send=not args.dry_run)
        scan_and_notify(dry_run=args.dry_run, min_score=args.min_score)
    elif args.command == "poll":
        check_config()
        logger.info("Callback polling 開始 (Ctrl+C で終了)")
        try:
            poll_callbacks()
        except KeyboardInterrupt:
            logger.info("停止しました")


if __name__ == "__main__":
    main()
