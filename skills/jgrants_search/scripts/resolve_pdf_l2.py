#!/usr/bin/env python3
"""
resolve_pdf_l2.py — 認証ブラウザ(Playwright)による公募要領PDF取得 (PDF発見ロジック L2)

jGrants 申請者ポータルの補助金詳細ページは、公募要領/申請様式が「.file-list」div に
ログイン後のみ描画される。ログイン済み storage_state を再利用して .file-list のPDFを
抽出・ダウンロードし、public.grants.attachment_urls に登録し、オプションで extract_pdf を呼ぶ。

動作モード:
  harvest --grant-id N --session <storage_state.json>   # ヘッドレスでPDF抽出・DL・登録
  login   --session-out <storage_state.json>            # ヘッドfulでログイン→ state保存(初回のみ)

クレデンシャル方針: パスワードは入力・保存しない。ユーザーが headful ログインする。
usage:
  env -u PYTHONPATH uv run skills/jgrants_search/scripts/resolve_pdf_l2.py harvest --grant-id 20 --session .cache/jgrants_state.json
"""

import argparse
import json
import logging
import os
import re
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

# 優先度: 公募要領 > 実施要領 > 申請要領 > 様式
PRIORITY = ["公募要領", "実施要領", "申請要領", "募集要領", "実施要綱"]
EXCLUDE = ["チラシ", "PR版", "概要"]


def _l2_extract_file_list(page) -> list:
    """.file-list 内の <a> を [(url, label)] で収集。"""
    items = []
    anchors = page.query_selector_all(".file-list a")
    for a in anchors:
        try:
            href = a.get_attribute("href")
            text = (a.inner_text() or "").strip()
            if href:
                items.append((href, text))
        except Exception:
            continue
    return items


def pick_pdf(items: list) -> str:
    """キーワード優先で最良のPDF URLを選定。"""
    best, best_score = None, -1
    for url, text in items:
        score = 0
        for i, kw in enumerate(PRIORITY):
            if kw in text or kw in url:
                score += (len(PRIORITY) - i) * 10
        if any(k in text for k in EXCLUDE):
            score -= 50
        if score > best_score:
            best, best_score = url, score
    return best


def harvest(page, grant: dict, download_dir: Path, session) -> dict:
    detail_url = DETAIL_TMPL.format(source_grant_id=grant["source_grant_id"])
    logger.info("pages を開く: %s", detail_url)
    page.goto(detail_url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)

    items = _l2_extract_file_list(page)
    if not items:
        return {"ok": False, "message": "file-list にPDFが無い(非ログイン or 未公開)",
                "grant_id": grant["id"], "url": detail_url}

    best = pick_pdf(items)
    if not best:
        return {"ok": False, "message": "公募要領PDFを判定できず", "all": items[:10]}

    # 認証セッションでDL(クッキー継承)
    resp = page.request.get(best, timeout=60000)
    if resp.status != 200:
        return {"ok": False, "message": f"DL失敗 HTTP{resp.status}", "url": best}
    body = resp.body()
    if not body.startswith(b"%PDF"):
        return {"ok": False, "message": "取得物がPDFでない(マジックバイト不一致)", "url": best,
                "len": len(body)}

    pdf_path = download_dir / f"grant{grant['id']}_kobou.pdf"
    pdf_path.write_bytes(body)
    logger.info("PDF保存: %s (%d bytes)", pdf_path, len(body))

    return {"ok": True, "grant_id": grant["id"], "url": best, "local_path": str(pdf_path),
            "bytes": len(body), "label": [t for u, t in items if u == best]}


def main():
    parser = argparse.ArgumentParser(description="認証ブラウザで公募要領PDF取得 (L2)")
    sub = parser.add_subparsers(dest="action", required=True)

    p_harvest = sub.add_parser("harvest")
    p_harvest.add_argument("--grant-id", type=int, required=True)
    p_harvest.add_argument("--session", required=True, help="ログイン済み storage_state.json")
    p_harvest.add_argument("--download-dir", default="data/pdfs")
    p_harvest.add_argument("--run-extract", action="store_true", help="DL後 extract_pdf を実行")

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
            print("\n🔐 jGrants にログインしてください。ログイン完了後、このターミナルで Enter を押してください。")
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
        result = harvest(page, grant, download_dir, args.session)

    if result["ok"]:
        # attachment_urls に追加 & 保存
        with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE public.grants SET attachment_urls = array_append(attachment_urls, %s), updated_at=NOW() WHERE id=%s AND NOT (%s = ANY(attachment_urls));",
                            (result["url"], grant["id"], result["url"]))
                conn.commit()
        print("\n✅ 公募要領PDF取得・登録")
        print(f"   URL: {result['url']}")
        print(f"   保存: {result['local_path']} ({result['bytes']} bytes)")
        if args.run_extract:
            import subprocess
            subprocess.run([sys.executable,
                            str(Path(__file__).resolve().parent / "extract_pdf.py"),
                            "--grant-id", str(grant["id"]), "--json"], check=False)
    else:
        print("\n❌ " + result["message"])
        if result.get("all"):
            for u, t in result["all"]:
                print(f"   - {t!r} {u[:100]}")
        print(f"   URL: {result.get('url','')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
