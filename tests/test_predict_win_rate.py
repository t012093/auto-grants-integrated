import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

from predict_win_rate import (  # noqa: E402
    load_weights,
    compute_rank,
    score_axis,
    build_improvement_notes,
    _score_track_record,
)


@pytest.fixture
def weights():
    return load_weights(str(_REPO / "skills/grant_lifecycle_manager/win_rate_weights.json"))


@pytest.fixture
def npo():
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "テストNPO",
        "category": "EDUCATION",
        "description": "子ども向けプログラミング教育を実施する新しいモデル事業。市民企業との連携で地域課題に取り組み、統計データに基づくニーズ分析を行う。",
        "target_audience": ["子ども", "保護者"],
        "activity_areas": ["教育", "デジタル"],
        "annual_budget": 10000000,
        "organization_type": "NPO_CORPORATION",
        "prepared_documents": ["ARTICLES"],
    }


@pytest.fixture
def grant():
    return {
        "id": 1,
        "title": "テスト助成金",
        "provider": "テスト財団",
        "amount_max": 3000000,
        "detail_text": "新規・先進的なモデル事業を公募。地域企業との連携体制で実施。統計データの提示。継続計画を評価。",
    }


# --- load_weights ---
def test_weights_version_and_sum():
    w = load_weights()
    assert "version" in w
    assert w["version"] == 1
    # 重みの合計が 1.00
    total = sum(a["weight"] for a in w["axes"].values())
    assert abs(total - 1.0) < 1e-6
    assert set(w["axes"].keys()) == {
        "funder_intent", "severity", "feasibility", "uniqueness",
        "scalability", "budget", "sustainability", "track_record",
    }


# --- compute_rank (coverage / rank / provisional) ---
def test_compute_rank_full_evaluation(weights):
    axes = {
        "funder_intent": {"score": 0.9, "evaluated": True},
        "severity": {"score": 0.8, "evaluated": True},
        "feasibility": {"score": 0.8, "evaluated": True},
        "uniqueness": {"score": 0.7, "evaluated": True},
        "scalability": {"score": 0.7, "evaluated": True},
        "budget": {"score": 0.9, "evaluated": True},
        "sustainability": {"score": 0.6, "evaluated": True},
        "track_record": {"score": 0.5, "evaluated": True},
    }
    comp = compute_rank(weights, axes)
    assert comp["insufficient_data"] is False
    assert comp["coverage"] == pytest.approx(1.0, abs=1e-4)
    assert comp["provisional"] is False
    assert comp["overall_score"] > 0


def test_compute_rank_partial_coverage_provisional(weights):
    # 評価済み軸が少なく coverage < 0.5 -> provisional
    axes = {
        "funder_intent": {"score": 0.9, "evaluated": True},
        "severity": {"score": None, "evaluated": False},
        "feasibility": {"score": None, "evaluated": False},
        "uniqueness": {"score": None, "evaluated": False},
        "scalability": {"score": None, "evaluated": False},
        "budget": {"score": None, "evaluated": False},
        "sustainability": {"score": None, "evaluated": False},
        "track_record": {"score": None, "evaluated": False},
    }
    comp = compute_rank(weights, axes)
    assert comp["coverage"] == pytest.approx(0.2, abs=1e-4)  # funder_intent のみ 0.20
    assert comp["provisional"] is True


def test_compute_rank_no_data_insufficient(weights):
    axes = {c: {"score": None, "evaluated": False} for c in weights["axes"]}
    comp = compute_rank(weights, axes)
    assert comp["insufficient_data"] is True


def test_compute_rank_rank_boundaries(weights):
    def _full(scores):
        return {c: {"score": s, "evaluated": True} for c, s in zip(weights["axes"], scores)}
    r_a = compute_rank(weights, _full([0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9]))
    assert r_a["rank"] == "A"
    r_d = compute_rank(weights, _full([0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]))
    assert r_d["rank"] == "D"


# --- score_axis: 新規軸 (確定的ルール) ---
def test_score_uniqueness_high(npo, grant):
    ax = score_axis("uniqueness", npo, grant, {})
    assert ax["evaluated"] is True
    assert ax["score"] == 1.0


def test_score_feasibility(npo, grant):
    ax = score_axis("feasibility", npo, grant, {})
    assert ax["evaluated"] is True
    assert ax["score"] >= 0.5  # 連携+体制 語のヒット数に応じた段階点


def test_score_severity(npo, grant):
    ax = score_axis("severity", npo, grant, {})
    assert ax["evaluated"] is True
    assert ax["score"] >= 0.6


def test_score_sustainability_missing_budget():
    npo0 = {"annual_budget": 0}
    ax = score_axis("sustainability", npo0, {"amount_max": 100}, {})
    assert ax["evaluated"] is False


def test_score_scalability(npo, grant):
    ax = score_axis("scalability", npo, grant, {})
    assert ax["evaluated"] is True
    assert ax["score"] > 0


# --- score_axis: 再利用軸 (eligibility report) ---
def test_score_funder_intent_reuse():
    npo = {}; grant = {}
    eligibility_axes = {"sem_purpose": {"score": 0.85, "evaluated": True, "evidence": "一致"}}
    ax = score_axis("funder_intent", npo, grant, eligibility_axes)
    assert ax["evaluated"] is True
    assert ax["score"] == pytest.approx(0.85)
    assert ax["source"] == "eligibility/sem_purpose"


def test_score_funder_intent_not_evaluated():
    ax = score_axis("funder_intent", {}, {}, {"sem_purpose": {"evaluated": False}})
    assert ax["evaluated"] is False


def test_score_budget_reuse_fallback_expense():
    # budget 未評価なら expense にフォールバック
    eligibility_axes = {"budget": {"evaluated": False}, "expense": {"score": 0.9, "evaluated": True}}
    ax = score_axis("budget", {}, {}, eligibility_axes)
    assert ax["evaluated"] is True
    assert ax["score"] == pytest.approx(0.9)
    assert ax["source"] == "eligibility/expense"


# --- track_record ---
def test_track_record_from_json():
    npo = {"track_records": [
        {"grant_name": "A", "award_year": 2025, "award_amount": 100},
        {"grant_name": "B", "award_year": 2024, "award_amount": 200},
    ]}
    ax = _score_track_record(npo)
    assert ax["evaluated"] is True
    assert ax["source"] == "track_records"


def test_track_record_no_data():
    ax = _score_track_record({"track_records": []})
    assert ax["evaluated"] is False


def test_track_record_from_applications():
    npo = {"_awarded_count": 2}
    ax = _score_track_record(npo)
    assert ax["evaluated"] is True
    assert ax["score"] > 0


# --- improvement_notes ---
def test_build_improvement_notes_top3():
    axes = {
        "sustainability": {"score": 0.1, "evaluated": True},
        "feasibility": {"score": 0.2, "evaluated": True},
        "severity": {"score": 0.3, "evaluated": True},
        "funder_intent": {"score": 0.9, "evaluated": True},
    }
    notes = build_improvement_notes(axes)
    assert len(notes) == 3
    # 下位3軸のみ
    assert {n["axis"] for n in notes} == {"sustainability", "feasibility", "severity"}
