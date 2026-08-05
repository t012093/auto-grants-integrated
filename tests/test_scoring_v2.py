"""スコアリング再設計 S1/S2 の機械軸ユニットテスト (tests/test_scoring_v2.py)

TwoLayerScorer の純粋ロジック(DB不要)を検証する。
RAG/expense 軸は DB 依存のため、ここでは budget/docs の機械軸とデータ欠落挙動を担保する。
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "grant_eligibility_checker" / "scripts"))

from eligibility_v2 import TwoLayerScorer


def make_scorer():
    # db_url は実接続されない(純ロジック限定のテスト)
    return TwoLayerScorer("postgresql://dummy/dummy")


def test_mech_budget_within_half_is_full_marks():
    s = make_scorer()
    npo = {"annual_budget": 10_000_000}
    grant = {"amount_max": 4_000_000}
    score, evaluated, evidence = s._mech_budget(npo, grant)
    assert evaluated is True
    assert score == 1.0
    assert "4,000,000" in evidence


def test_mech_budget_over_limit_penalized():
    s = make_scorer()
    npo = {"annual_budget": 10_000_000}
    grant = {"amount_max": 10_000_000}  # 比率 1.0 → 0 点
    score, evaluated, _ = s._mech_budget(npo, grant)
    assert evaluated is True
    assert score == 0.0


def test_mech_budget_missing_data_unevaluated():
    s = make_scorer()
    assert s._mech_budget({"annual_budget": 0}, {"amount_max": 0})[1] is False
    assert s._mech_budget({"annual_budget": 10_000_000}, {"amount_max": None})[1] is False


def test_mech_docs_empty_required_is_unevaluated_not_100():
    """必要書類が未抽出(空)のときは evaluated=False で、100点扱いにしない(旧G6のバグ対策)。"""
    s = make_scorer()
    npo = {"prepared_documents": ["ARTICLES"]}
    grant = {"required_documents": []}
    score, evaluated, evidence = s._mech_docs(npo, grant)
    assert evaluated is False
    assert score is None
    assert "未抽出" in evidence


def test_mech_docs_partial_match():
    s = make_scorer()
    npo = {"prepared_documents": ["ARTICLES", "FINANCIAL_REPORT"]}
    grant = {"required_documents": ["ARTICLES", "FINANCIAL_REPORT", "BOARD_LIST"]}
    score, evaluated, _ = s._mech_docs(npo, grant)
    assert evaluated is True
    assert abs(score - 2 / 3) < 1e-9


def test_default_axes_weights_sum_to_one():
    s = make_scorer()
    total = sum(a["weight"] for a in s.axes_config.values())
    assert abs(total - 1.0) < 1e-9


def test_coverage_threshold_default():
    s = make_scorer()
    assert s.coverage_threshold == 0.6
    assert s.thresholds["eligible"] == 75
    assert s.thresholds["conditional"] == 55
