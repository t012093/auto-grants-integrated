#!/usr/bin/env python3
"""
resolve_pdf_l2.py — 認証ブラウザ(Playwright)による公募要領PDF取得 (PDF発見ロジック L2)

jGrants 申請者ポータルの補助金詳細ページは、公募要領/申請様式が「.file-list」div に
ログイン後のみ描画される。`<a href="#">`（JS駆動）なので対象アンカーをクリックし
download イベントを捕捉。公募要領の形態(pdf直/zip)に対応し、公募要領PDFを抽出・保存。

形態対応:
  - .pdf   → そのまま保存(交付要綱/公募要領/募集要項をキーワード優先で選定)
  - .zip   → ダウンロード→解凍→中から公募要領PDFを選定
動作モード:
  harvest --grant-id N --session <storage_state.json>   # 取得・保存・(op)extract
  login   --session-out <storage_state.json>            # headfulログイン→state保存(初回)
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import zipfile
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

PRIORITY = ["公募要領", "交付要綱", "募集要項", "実施要領", "応募要領", "申請要領"]
EXCLUDE = ["チラシ", "PR版", "概要", "記入例", "よくある質問", "参考"]


def _title(anchor) -> str:
    return (anchor.get_attribute("title") or anchor.inner_text() or "").strip()


def score_title(title: str) -> int:
    s = 0
    low = title.lower()
    if low.endswith(".pdf"):
        s += 4
    elif low.endswith(".zip"):
        s += 1
    for i, kw in enumerate(PRIORITY):
        if kw in title:
            s += (len(PRIORITY) - i) * 10
    if any(k in title for k in EXCLUDE):
        s -= 60
    return s


def wait_file_anchors(page, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            anchors = page.query_selector_all(".file-list a")
            if anchors:
                return anchors
        except Exception as e:
            # Queue-It などによるナビゲーションで実行コンテキスト破棄 → 待って再試行
            logger.debug("querySelectorAll retry (%s): %s", round(time.time() - t0), str(e)[:80])
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
        page.wait_for_timeout(1500)
    return []


def pick_best_anchor(anchors):
    best, bs = None, -1
    for a in anchors:
        sc = score_title(_title(a))
        if sc > bs:
            best, bs = a, sc
    return best, bs


def download_anchor(page, anchor, dest_dir: Path, grant_id: int) -> dict:
    title = _title(anchor) or "file"
    try:
        with page.expect_download(timeout=60000) as di:
            anchor.scroll_into_view_if_needed()
            anchor.click(force=True)
        dl = di.value
    except Exception as e:
        return {"ok": False, "message": f"download捕捉失敗: {e}", "title": title}
    safe = re.sub(r"[^\w.\-]+", "_", Path(dl.suggested_filename or title).stem)[:60]
    path = dest_dir / f"grant{grant_id}_{safe}{Path(dl.suggested_filename or title).suffix}"
    dl.save_as(str(path))
    return {"ok": True, "path": str(path), "suggested": dl.suggested_filename,
            "bytes": path.stat().st_size, "title": title, "ext": path.suffix.lower()}


def extract_pdf_from_zip(zip_path: Path, dest_dir: Path, grant_id: int, zip_title: str) -> dict:
    """zip を解凍し、中から最良の公募要領PDFを選定。パストラバーサル/zip bomb対策込み。"""
    exdir = dest_dir / f"grant{grant_id}_unzip"
    exdir_root = exdir.resolve()
    exdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        members = z.infolist()
        # zip bomb 防御: 件数・総展開サイズに上限
        if len(members) > 500 or sum(i.file_size for i in members) > 500 * 1024 * 1024:
            return {"ok": False, "message": f"zipの件数/展開サイズが想定外のため中止 ({zip_title})"}
        for member in members:
            if member.is_dir():
                continue
            # メンバー名を basename のみに正規化して `../`・絶対パスを排除 (CWE-22/23)
            name = Path(member.filename.replace("\\", "/")).name
            if not name:
                continue
            dest = (exdir_root / name).resolve()
            if not dest.is_relative_to(exdir_root):
                logger.warning("zipに異常パス(トラバーサル)を検出、スキップ: %s", member.filename)
                continue
            with z.open(member) as src, open(dest, "wb") as out:
                out.write(src.read())
    pdfs = [p for p in exdir.rglob("*") if p.suffix.lower() == ".pdf"]
    if not pdfs:
        return {"ok": False, "message": f"zip内にPDF無し ({zip_title})"}
    best, bs = None, -1
    for p in pdfs:
        sc = score_title(p.stem)
        if sc > bs:
            best, bs = p, sc
    if not best:
        best = pdfs[0]
    return {"ok": True, "path": str(best), "bytes": best.stat().st_size, "title": best.stem}


def harvest(page, grant: dict, download_dir: Path) -> dict:
    detail_url = DETAIL_TMPL.format(source_grant_id=grant["source_grant_id"])
    logger.info("pages を開く: %s", detail_url)
    page.goto(detail_url, wait_until="domcontentloaded", timeout=45000)
    anchors = wait_file_anchors(page)
    if not anchors:
        return {"ok": False, "message": "file-list に項目が無い(非ログイン/未公開/要領は別所)",
                "grant_id": grant["id"]}
    anchor, sc = pick_best_anchor(anchors)
    if anchor is None or sc <= 0:
        titles = [_title(a)[:40] for a in anchors]
        return {"ok": False, "message": "要領PDFを判定できず", "grant_id": grant["id"], "all": titles}

    res = download_anchor(page, anchor, download_dir, grant["id"])
    if not res["ok"]:
        return {**res, "grant_id": grant["id"]}
    # zip なら解凍して中から公募要領PDF
    if res["ext"] == ".zip":
        zres = extract_pdf_from_zip(Path(res["path"]), download_dir, grant["id"], res["title"])
        if not zres["ok"]:
            return {**zres, "grant_id": grant["id"]}
        res = {**res, **zres}
    # PDF検証
    data = Path(res["path"]).read_bytes()
    if not data.startswith(b"%PDF"):
        return {"ok": False, "grant_id": grant["id"], "message": "公募要領がPDFでない",
                "path": res["path"], "ext": res.get("ext"), "bytes": res.get("bytes")}
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
        sys.exit(0 if result["ok"] else 1)

    if result["ok"]:
        with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE public.grants SET attachment_urls = array_append(attachment_urls, %s), updated_at=NOW() "
                            "WHERE id=%s AND NOT (%s = ANY(attachment_urls));",
                            (result["path"], grant["id"], result["path"]))
                conn.commit()
        print("\n✅ 公募要領PDF取得・保存")
        print(f"   取得: {result.get('title','')} ({result['bytes']} bytes)")
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
