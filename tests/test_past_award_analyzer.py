import pytest
from skills.past_award_analyzer.scripts.analyze_past_awards import PastAwardAnalyzer

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
