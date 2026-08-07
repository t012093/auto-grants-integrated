#!/usr/bin/env python3
"""
monitor_gmail_awards.py — 採択通知メールの Gmail 監視 (Phase C・骨格)

自社の応募助成金の「採択/不採択」通知メールを Gmail から検出し、
grant_applications に記録する監視の**骨格**を提供する。

⚠️ 注意: これは骨格であり、実運用には Gmail API / IMAP の認証情報が必要。
  認証方式 (Gmail API OAuth2 または IMAP) ごとに _connect_mailbox() を実装すること。
  本スクリプトは単体で認証情報を持たないため、_connect_mailbox() 未実装のままでは
  IMAP 接続に進まない (明示的にエラーを返す = 偽成功しない、AGENTS.md モック禁止準拠)。

動作:
  1. _connect_mailbox(): メールボックス接続 (要・認証実装)
  2. _detect_result(subject, body): 件名/本文から採択(AWARDED) or 不採択(REJECTED) を判定
  3. _match_application(): メール本文から grant と npo を対応付け
  4. 判定結果を grant_applications に記録

実装予定 (次フェーズ):
  - Gmail API OAuth2 (google-api-python-client) または IMAP (imaplib) の認証
  - 採択通知メールの本文パース
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


# ---------------------------------------------------------------------------
# 1. メールボックス接続 (骨格・要認証実装)
# ---------------------------------------------------------------------------
def _connect_mailbox():
    """Gmail メールボックスへ接続する。認証未実装のため明示的に NotImplementedError。

    実運用時はここで:
      - Gmail API: googleapiclient.discovery.build('gmail','v1', credentials=creds)
      - IMAP:      imaplib.IMAP4_SSL('imap.gmail.com') にログイン
    を実装する。認証情報は .env (GMAIL_* 変数) を読む。
    """
    raise NotImplementedError(
        "Gmail 監視は骨格のみ。GMAIL_* 認証情報と _connect_mailbox() の実装が必要です。"
        "手動取込は scripts/record_application_result.py を使用してください。"
    )


# ---------------------------------------------------------------------------
# 2. 採択/不採択 判定 (ルールベース・ハルシネーション防止)
# ---------------------------------------------------------------------------
AWARD_KEYWORDS = ["採択", "選定", "助成決定", "採択通知", "採択され", "交付決定", "選ばれ"]
REJECT_KEYWORDS = ["不採択", "落選", "選外", "不採用", "厳正な審査の結果、採択に至らなかった"]
PENDING_KEYWORDS = ["審査中", "審査結果は", "審査の結果を"]

UNKNOWN_RESULT = "UNKNOWN"


def _detect_result(subject: str = "", body: str = "") -> str:
    """件名・本文から 採択(AWARDED)/不採択(REJECTED)/審査中(PENDING)/不明 を判定。

    判定不能は UNKNOWN (絶対に採択/不採択と断定しない = 偽成功防止)。
    """
    text = f"{subject or ''}\n{body or ''}".lower()
    # 否定語 (不採用・不採択) を優先 (誤って「採択」と判定しない)
    if any(k in text for k in REJECT_KEYWORDS):
        return "REJECTED"
    if any(k in text for k in AWARD_KEYWORDS):
        return "AWARDED"
    if any(k in text for k in PENDING_KEYWORDS):
        return "PENDING"
    return UNKNOWN_RESULT


# ---------------------------------------------------------------------------
# 3. メールと grant / npo の対応付け (骨格)
# ---------------------------------------------------------------------------
def _extract_grant_id(body: str, known_grants: dict) -> Optional[int]:
    """メール本文から助成金 ID を推定。known_grants は {title: id} 辞書。

    実運用ではメール本文の助成金名と DB の grants を照合する。骨格は戻さない。
    """
    if not known_grants:
        return None
    for title, gid in known_grants.items():
        if title and title in (body or ""):
            return gid
    return None


# ---------------------------------------------------------------------------
# 4. 監視実行 (骨格)
# ---------------------------------------------------------------------------
def run_monitor(known_grants: Optional[dict] = None,
                npo_profile_id: Optional[str] = None,
                db_url: Optional[str] = None) -> dict:
    """モニタリングを実行する。認証未実装のため NotImplementedError を raise (偽成功しない)。

    実運用時は:
      1. _connect_mailbox() で未読の採択通知メールを取得
      2. 各メールを _detect_result で判定
      3. _extract_grant_id で grant 対応付け
      4. record_application_result.record_result で grant_applications に記録
    を実装する。
    """
    raise NotImplementedError(
        "Gmail 監視の実行は骨格のみ。_connect_mailbox() と認証情報の実装が必要です。"
    )


def main():
    parser = argparse.ArgumentParser(description="採択通知メールの Gmail 監視 (Phase C・骨格)")
    parser.add_argument("--org-id", help="NPO Profile UUID")
    parser.add_argument("--json", action="store_true", help="JSON 出力")
    args = parser.parse_args()

    try:
        run_monitor(npo_profile_id=args.org_id)
    except NotImplementedError as e:
        print(f"ℹ️ {e}")
        print("  ※ Gmail 監視は認証実装待ちの骨格です。採択結果の手動記録は:")
        print("     uv run scripts/record_application_result.py --org-id <uuid> --grant-id <id> --result AWARDED/REJECTED")
        if args.json:
            print(json.dumps({"status": "skeleton", "error": str(e)}, ensure_ascii=False))
        sys.exit(2)


if __name__ == "__main__":
    main()
