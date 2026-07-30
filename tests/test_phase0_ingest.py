"""
Phase 0 インジェスト基盤のユニットテスト (test_phase0_ingest.py)
"""

import json
import pytest
from backend.extract.css_extractor import CSSExtractor, DropRecord
from backend.normalize.negation_matcher import has_matching_keyword_without_negation

SAMPLE_HTML = """
<html>
<body>
  <ul class="grant-list">
    <li class="item">
      <h3 class="title"><a href="https://example.com/grant1">令和8年度 NPO助成金</a></h3>
      <span class="date">2026-09-30</span>
    </li>
    <li class="item">
      <!-- タイトルがない不正な要素 -->
      <span class="date">2026-10-15</span>
    </li>
    <li class="item">
      <h3 class="title"><a href="https://example.com/grant2">地域コミュニティ再生支援</a></h3>
      <span class="date">2026-11-01</span>
    </li>
  </ul>
  <div class="main-content">
    <p>応募するボタンはこちら</p>
    <p>本助成金は地域福祉の推進を目的としています。</p>
  </div>
</body>
</html>
"""

SAMPLE_PROFILE = {
    "source_id": "test_foundation",
    "selectors": {
        "list_selector": "ul.grant-list > li.item",
        "title_selector": "h3.title a",
        "url_selector": "h3.title a",
        "deadline_selector": "span.date",
        "provider_default": "テスト助成財団"
    }
}

def test_css_extractor_deterministic_and_drop_record():
    extractor = CSSExtractor()
    grants, drops = extractor.extract_list(SAMPLE_HTML, SAMPLE_PROFILE)

    # 確定的抽出の確認
    assert len(grants) == 2
    assert grants[0].title == "令和8年度 NPO助成金"
    assert grants[0].provider == "テスト助成財団"
    assert grants[0].url == "https://example.com/grant1"
    assert grants[0].is_deterministic is True

    # DropRecord ガードレールの確認 (1件ドロップ)
    assert len(drops) == 1
    assert drops[0].reason == "missing_title"
    assert drops[0].source_id == "test_foundation"

def test_html_to_markdown_clean():
    extractor = CSSExtractor()
    md = extractor.html_to_markdown_clean(SAMPLE_HTML, "div.main-content")
    
    # ノイズ行 "応募するボタンはこちら" が除去されていること
    assert "応募する" not in md
    assert "地域福祉の推進" in md

def test_negation_matcher():
    # 正常系（肯定）
    assert has_matching_keyword_without_negation("本助成金はNPO法人が対象です", ["NPO法人"]) is True
    
    # 異常系（否定構文）
    assert has_matching_keyword_without_negation("NPO法人以外は対象外です", ["NPO法人"]) is False
    assert has_matching_keyword_without_negation("事前登録は不要です", ["事前登録"]) is False
