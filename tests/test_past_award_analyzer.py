import pytest
from skills.past_award_analyzer.scripts.analyze_past_awards import PastAwardAnalyzer
from skills.past_award_analyzer.scripts.crawl_past_awards import parse_amount, parse_year, PastAwardCollector

def test_analyze_past_awards_empty():
    analyzer = PastAwardAnalyzer(db_url=None)
    result = analyzer.analyze_records([])
    assert result["count"] == 0
    assert result["budget_range"]["avg_amount"] == 0
    assert "note" in result

def test_analyze_past_awards_calculation():
    analyzer = PastAwardAnalyzer(db_url=None)
    mock_records = [
        {
            "award_amount": 400000,
            "project_title": "AIとDXを活用した子ども向けプログラミング教室",
            "project_summary": "地域NPOと連携し、子どもへのデジタル教育を提供",
            "evaluation_comment": "地域密着と継続性を高く評価"
        },
        {
            "award_amount": 300000,
            "project_title": "留学生インターン受入れと国際交流",
            "project_summary": "大学と連携した国際交流事業",
            "evaluation_comment": "波及効果が大きい"
        }
    ]
    result = analyzer.analyze_records(mock_records)
    assert result["count"] == 2
    assert result["budget_range"]["avg_amount"] == 350000
    assert result["budget_range"]["max_amount"] == 400000
    assert result["budget_range"]["min_amount"] == 300000
    assert result["solution_model"]["collaboration_rate"] == 1.0
    assert "継続性" in result["evaluator_feedback"]["top_keywords"]

def test_parse_amount_and_year():
    assert parse_amount("助成金額：1,000万円") == 10000000
    assert parse_amount("上限 500,000円") == 500000
    assert parse_amount("10,000千円") == 10000000
    
    assert parse_year("2025年度 助成事業") == 2025
    assert parse_year("令和7年 採択結果") == 2025
    assert parse_year("平成30年 助成一覧") == 2018

def test_extract_records_from_html():
    collector = PastAwardCollector(db_url=None)
    sample_html = """
    <html>
      <body>
        <ul class="grant-list">
          <li>
            <h3 class="title">特定非営利活動法人学びの場：子ども向けDXプログラミング教育事業</h3>
            <span class="date">令和7年度</span>
            <span class="amount">1,000万円</span>
          </li>
        </ul>
      </body>
    </html>
    """
    profile = {
        "source_name": "テスト財団",
        "acquisition_method": "test_crawler",
        "selectors": {
            "list_selector": "ul.grant-list > li",
            "title_selector": "h3.title"
        }
    }
    records = collector.extract_records_from_html(sample_html, profile)
    assert len(records) == 1
    assert records[0]["recipient_name"] == "特定非営利活動法人学びの場"
    assert records[0]["project_title"] == "子ども向けDXプログラミング教育事業"
    assert records[0]["award_amount"] == 10000000
    assert records[0]["award_year"] == 2025
