"""harvest_grant_pdfs の PDF 選定ロジック単体テスト (networK不要)。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "jgrants_search" / "scripts"))

from harvest_grant_pdfs import extract_pdf_links, pick_best_pdf  # noqa: E402


def test_extract_pdf_links_abs_urls():
    html = '<a href="https://x.example/a.pdf">要領(PDF:10KB)</a> <a href="/b.pdf">b</a>'
    links = extract_pdf_links(html, "https://www.maff.go.jp/j/foo/")
    urls = [u for u, _ in links]
    assert "https://x.example/a.pdf" in urls
    # relative resolved against base
    assert any("b.pdf" in u and "maff.go.jp" in u for u in urls)


def test_pick_best_prioritizes_yourei_over_pr():
    links = [
        ("https://x/promo.pdf", "中山間地域所得確保対策PRチラシ(PDF:891KB)"),
        ("https://x/kakuho-32.pdf", "中山間地域所得確保対策実施要領（令和7年12月16日一部改正）(PDF:452KB)"),
        ("https://x/kakuho-33.pdf", "中山間地域所得確保対策の概要(PDF:2760KB)"),
    ]
    url, txt = pick_best_pdf(links)
    assert "kakuho-32" in url
    assert "実施要領" in txt


def test_pick_best_returns_none_when_no_pdf():
    url, txt = pick_best_pdf([])
    assert url is None and txt == ""
