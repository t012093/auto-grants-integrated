#!/usr/bin/env python3
"""
resolve_pdf_l2.py — 認証ブラウザ(Playwright)による公募要領PDF取得 (PDF発見ロジック L2)

jGrants 申請者ポータルの補助金詳細ページは、公募要領/申請様式が「.file-list」div に
ログイン後のみ描画される。`<a href="#">`（JS駆動）なので、対象アンカーをクリックし
download イベントを捕捉して保存する。ログイン済み storage_state を再利用(ヘッドレス)。

動作モード:
  harvest --grant-id N --session <storage_state.json>   # 公募要領PDF取得・保存・(doc)登録
  login   --session-out <storage_state.json>            # headfulログイン→state保存(初回)

クレデンシャル方針: パスワードは入力・保存しない。ユーザーが headful ログインする。
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg
import psycopg.rows
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("resolve_pdf_l2")

DETAIL_TMPL = "https://www.jgrants-portal.go.jp/subsidy/{source_grant_id}"
PORTAL_HOME = "https://www.jgrants-portal.go.jp/"

PRIORITY = ["公募要領", "交付要綱", "実施要領", "応募要領", "申請要領", "募集要領"]
EXCLUDE = ["チラシ", "PR版", "概要", "様式"]


def file_anchors(page):
    """.file-list 内の <a> を返す。"""
    return page.query_selector_all(".file-list a")


def pick_pdf_anchor(anchors):
    """公募要領PDFアンカーを選定 (.pdf & キーワード優先)。無ければ None。"""
    best, best_s = None, -1
    for a in anchors:
        title = (a.get_attribute("title") or a.inner_text() or "").strip()
        if not title.lower().endswith(".pdf"):
            continue
        score = 0
        for i, kw in enumerate(PRIORITY):
            if kw in title:
                score += (len(PRIORITY) - i) * 10
        if any(k in title for k in EXCLUDE):
            score -= 50
        if score > best_s:
            best, best_s = a, score
    return best


def download_anchor(page, anchor, dest_dir: Path, grant_id: int):
    title = (anchor.get_attribute("title") or "").strip() or "kobou.pdf"
    try:
        with page.expect_download(timeout=60000) as di:
            anchor.scroll_into_view_if_needed()
            anchor.click(force=True)
        dl = di.value
    except Exception as e:
        return {"ok": False, "message": f"download捕捉失敗: {e}", "title": title}
    safe = re.sub(r"[^\w.\-]+", "_", Path(dl.suggested_filename or title).stem)
    path = dest_dir / f"grant{grant_id}_{safe}.pdf"
    dl.save_as(str(path))
    data = path.read_bytes()
    return {"ok": True, "path": str(path), "suggested": dl.suggested_filename,
            "bytes": len(data), "is_pdf": data.startswith(b"%PDF"), "title": title}


def harvest(page, grant: dict, download_dir: Path) -> dict:
    detail_url = DETAIL_TMPL.format(source_grant_id=grant["source_grant_id"])
    logger.info("pages を開く: %s", detail_url)
    page.goto(detail_url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(8000)

    anchors = file_anchors(page)
    if not anchors:
        return {"ok": False, "message": "file-list に項目が無い(非ログイン or 未公開)", "grant_id": grant["id"]}

    pdf_anchor = pick_pdf_anchor(anchors)
    if not pdf_anchor:
        titles = [(a.get_attribute("title") or a.inner_text() or "").strip()[:40] for a in anchors]
        return {"ok": False, "message": "公募要領PDF(.pdf)アンカーなし", "grant_id": grant["id"], "all": titles}

    res = download_anchor(page, pdf_anchor, download_dir, grant["id"])
    if not res["ok"]:
        return {**res, "grant_id": grant["id"]}
    if not res["is_pdf"]:
        return {"ok": False, "grant_id": grant["id"], "message": "取得物がPDFでない",
                "path": res["path"], "bytes": res["bytes"], "title": res["title"]}
    return {"ok": True, "grant_id": grant["id"], **res}


def main():
    parser = argparse.ArgumentParser(description="認証ブラウザで公募要領PDF取得 (L2)")
    sub = parser.add_subparsers(dest="action", required=True)

    p_harvest = sub.add_parser("harvest")
    p_harvest.add_argument("--grant-id", type=int, required=True)
    p_harvest.add_argument("--session", required=True)
    p_harvest.add_argument("--download-dir", default="data/pdfs")
    p_harvest.add_argument("--run-extract", action="store_true")
    p_harvest.add_argument("--json", action="store_true")

    p_login = sub.add_parser("login")
    p_login.add_argument("--session-out", required=True)
    args = parser.parse_args()

    if not DATABASE_URL:
        print("❌ DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    if args.action == "login":
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(PORTAL_HOME, wait_until="domcontentloaded")
            print("\n🔐 jGrants にログインしてください(gBizID)。完了後、このターミナルで Enter。")
            input(">>> Enter でセッション保存: ")
            out = Path(args.session_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(out))
            print(f"✅ storage_state 保存: {out}")
            browser.close()
        sys.exit(0)

    # harvest
    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, source_grant_id, title, attachment_urls FROM public.grants WHERE id=%s;",
                        (args.grant_id,))
            grant = cur.fetchone()
    if not grant:
        print("❌ grant なし", file=sys.stderr)
        sys.exit(1)

    download_dir = Path(args.download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(Path(args.session)))
        page = ctx.new_page()
        result = harvest(page, grant, download_dir)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if not result["ok"]:
            sys.exit(1)
        sys.exit(0)

    if result["ok"]:
        # ローカル保存パスを attachment_urls に登録(ダウンロードはセッション依存のためURL不固定)
        with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.grants SET attachment_urls = array_append(attachment_urls, %s), updated_at=NOW() "
                    "WHERE id=%s AND NOT (%s = ANY(attachment_urls));",
                    (result["path"], grant["id"], result["path"]))
                conn.commit()
        print("\n✅ 公募要領PDF取得・保存")
        print(f"   取得: {result['title']} ({result['bytes']} bytes)")
        print(f"   保存: {result['path']}")
        if args.run_extract:
            print("\n→ extract_pdf 実行")
            subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "extract_pdf.py"),
                            "--grant-id", str(grant["id"]), "--pdf-path", result["path"], "--json"], check=False)
    else:
        print("\n❌ " + result["message"])
        if result.get("all"):
            for t in result["all"]:
                print(f"   - {t}")
        sys.exit(1)


if __name__ == "__main__":
    main()
