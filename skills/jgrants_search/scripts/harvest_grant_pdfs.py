#!/usr/bin/env python3
"""
harvest_grant_pdfs.py — 公募要領 PDF の収集 (スコアリング再設計 S3-1)

対象助成金の公募要領 PDF URL を特定し、public.grants.attachment_urls に登録する。
extract_pdf.py --grant-id が attachment_urls[0] をダウンロードして深掘りする前提。

収集順 (docs/scoring_redesign_plan.md §J/P):
  1. detail_text 内の <a href> 直リンク (.pdf)
  2. 直リンク無しの場合、detail_text の「参照URL」/ details_url のページを crawl して .pdf を抽出
  3. キーワード優先スコアで「公募要領/実施要領/要綱」を選定 (チラシ/PR/概要は除外)

usage:
  env -u PYTHONPATH uv run skills/jgrants_search/scripts/harvest_grant_pdfs.py --grant-id 20 [--dry-run] [--verbose]
"""

import argparse
import json
import logging
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Tuple

import psycopg
import psycopg.rows
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("harvest_pdfs")

# キーワード優先 (前ほど高評価)。除外ワード
PRIORITY_KEYWORDS = ["公募要領", "実施要領", "募集要領", "申請要領", "実施要綱", "交付要綱", "応募要領"]
EXCLUDE_KEYWORDS = ["チラシ", "PR版", "概要", "リーフレット", "パンフレット", "別添", "様式"]
USER_AGENT = "Mozilla/5.0 (harvest_grant_pdfs; auto-grants-integrated)"


def extract_pdf_links(html: str, base_url: str) -> List[Tuple[str, str]]:
    """HTML から .pdf リンク (url, 周辺テキスト) を抽出。base_url で相対URL解決。"""
    links: List[Tuple[str, str]] = []
    # <a href="...">text</a>
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+\.pdf[^"\']*)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href = m.group(1).strip()
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        text = re.sub(r"\s+", " ", text).strip()
        full = href if href.startswith("http") else urllib.parse.urljoin(base_url, href)
        if full not in [u for u, _ in links]:
            links.append((full, text))
    # 素の .pdf URL (a タグ無し)
    for m in re.finditer(r'(https?://[^\s"\'<>]+\.pdf(?:\?[^\s"\'<>]*)?)', html):
        u = m.group(1)
        if u not in [x for x, _ in links]:
            links.append((u, ""))
    return links


def pick_best_pdf(links: List[Tuple[str, str]]) -> Tuple[str, str]:
    """キーワード優先スコアで最適な 公募要領 PDF を選定。"""
    best_url, best_txt, best_score = None, "", -1
    for url, text in links:
        score = 0
        for i, kw in enumerate(PRIORITY_KEYWORDS):
            if kw in text or kw in url:
                score += (len(PRIORITY_KEYWORDS) - i) * 10
        if any(k in text for k in EXCLUDE_KEYWORDS):
            score -= 50
        if re.search(r"\.pdf\s*:\s*\d+", text) and score > 0:  # "(PDF : xxxKB)" 付き
            score += 5
        if score > best_score:
            best_score, best_url, best_txt = score, url, text
    return best_url, best_txt


def fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "ignore")


def get_ref_url(detail_text: str, details_url: str) -> str:
    """detail_text 内の「参照URL」か details_url を返す。"""
    for m in re.finditer(r'<a[^>]+href=["\'](https?://[^"\']+)["\']', detail_text):
        href = m.group(1)
    # section 参照URL 直後のリンクを優先
    m = re.search(r"参照URL[\s\S]{0,200}?<a[^>]+href=[\"'](https?://[^\"']+)[\"']", detail_text)
    if m:
        return m.group(1)
    return details_url or ""


def harvest_for_grant(cur, grant_id: int) -> dict:
    cur.execute("SELECT id, title, detail_text, details_url, attachment_urls FROM public.grants WHERE id=%s;", (grant_id,))
    g = cur.fetchone()
    if not g:
        return {"ok": False, "message": f"grant {grant_id} なし"}
    detail_text = g.get("detail_text") or ""
    details_url = g.get("details_url") or ""
    cur_attach = list(g.get("attachment_urls") or [])

    found = []
    # 1. detail_text 直リンク
    direct = extract_pdf_links(detail_text, details_url or "https://www.jgrants-portal.go.jp/")
    for url, text in direct:
        found.append((url, text, "detail_text直リンク"))

    # 2. 参照ページ crawl (直リンクが公募要領らしくなければ)
    ref_url = get_ref_url(detail_text, details_url)
    if ref_url:
        try:
            page = fetch_page(ref_url)
            page_links = extract_pdf_links(page, ref_url)
            for url, text in page_links:
                if url not in [u for u, _, _ in found]:
                    found.append((url, text, f"参照ページ<{ref_url[:60]}>"))
        except Exception as e:
            logger.warning("参照ページ取得失敗 %s: %s", ref_url, e)

    # 選定
    best_url, best_txt = pick_best_pdf([(u, t) for u, t, _ in found])
    if not best_url:
        return {"ok": False, "grant_id": grant_id, "message": "公募要領PDFが見つかりませんでした",
                "found": [(u, t) for u, t, s in found]}

    if best_url not in cur_attach:
        cur_attach.append(best_url)
    return {"ok": True, "grant_id": grant_id, "selected_url": best_url,
            "selected_text": best_txt, "attachment_urls": cur_attach,
            "found": [(u, t[:40], s) for u, t, s in found]}


def main():
    parser = argparse.ArgumentParser(description="公募要領 PDF 収集 (attachment_urls 登録)")
    parser.add_argument("--grant-id", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true", help="DB書込せず選定結果のみ表示")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("❌ DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            result = harvest_for_grant(cur, args.grant_id)
            if not result["ok"]:
                print("❌ " + result["message"])
                for u, t, s in result.get("found", []):
                    print(f"   - {t!r} {u[:110]} ({s})")
                sys.exit(1)
            print("✅ 選定:", result["selected_text"])
            print("   URL:", result["selected_url"])
            print("   → attachment_urls:", result["attachment_urls"])
            if args.verbose:
                print("\n見つかったPDF:")
                for u, t, s in result["found"]:
                    print(f"   - {t!r} {u[:110]} ({s})")
            if not args.dry_run:
                cur.execute("UPDATE public.grants SET attachment_urls=%s, updated_at=NOW() WHERE id=%s;",
                            (result["attachment_urls"], args.grant_id))
                conn.commit()
                print(f"\n✅ grant {args.grant_id} の attachment_urls を更新しました (dry-runではない)")
            else:
                print("\n(--dry-run のため DB は更新していません)")


if __name__ == "__main__":
    main()
