"""task_human_ai_allocator (Phase 1: ルール主軸) のテスト"""
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

from task_human_ai_allocator import (  # noqa: E402
    detect_business_type,
    assign_tag,
    load_json,
    run,
    _build_offer,
    VALID_TAGS,
)

TEMPLATES = load_json("business_templates.json")
TAG_RULES = load_json("tag_rules.json")


# --- 事業型判定 (spec §4) ---
def test_detect_children_cafeteria():
    assert detect_business_type("子ども食堂を運営します", TEMPLATES) == "CHILDREN_CAFETERIA"


def test_detect_farm_it():
    assert detect_business_type("農家のスマート農業とEC出荷", TEMPLATES) == "FARM_IT"


def test_detect_general_fallback():
    """判定不能は GENERAL にフォールバック (勝手に特定の型へ断定しない)"""
    assert detect_business_type("全く別の内容の活動です", TEMPLATES) == "GENERAL"


def test_detect_empty_is_general():
    assert detect_business_type("", TEMPLATES) == "GENERAL"


# --- タグ判定 (spec §6) ---
def test_tag_human_priority():
    """HUMAN キーワードは最優先"""
    assert assign_tag("行政窓口との事前相談", TAG_RULES) == "HUMAN"


def test_tag_auto():
    assert assign_tag("PDFからテキストを抽出する", TAG_RULES) == "AUTO"


def test_tag_hybrid():
    assert assign_tag("企画書の最終承認", TAG_RULES) == "HYBRID"


def test_tag_unknown_fallback_hybrid():
    """不明なタスクは HYBRID にフォールバック (安全側・AUTOと断定しない)"""
    assert assign_tag("全く新しいタスク", TAG_RULES) == "HYBRID"
    assert assign_tag("", TAG_RULES) == "HYBRID"


def test_tag_preset_wins():
    """テンプレート指定(preset)はルールより優先"""
    assert assign_tag("行政窓口との事前相談", TAG_RULES, preset="HYBRID") == "HYBRID"


# --- 出力構成 (spec §9) ---
def test_build_offer_shape():
    proposal = {"id": "abc", "title": "テスト企画書", "content_markdown": "子ども食堂の運営"}
    alloc = json.loads(_build_offer("abc", proposal, TEMPLATES))
    assert alloc["proposal_id"] == "abc"
    assert alloc["business_type"] == "CHILDREN_CAFETERIA"
    assert "positions" in alloc and "tasks" in alloc
    assert all(p["position_code"] in ("PM", "LOCAL_DIR", "SITE_OP") for p in alloc["positions"])
    # 共通タスクを含む
    titles = [t["title"] for t in alloc["tasks"]]
    assert "企画書本文の最終チェック・承認" in titles


def test_build_offer_tasks_have_valid_tags():
    proposal = {"id": "abc", "title": "テスト", "content_markdown": "子ども食堂を運営"}
    alloc = json.loads(_build_offer("abc", proposal, TEMPLATES))
    for t in alloc["tasks"]:
        assert t["tag"] in VALID_TAGS
        assert t["assigned_position"] is not None


def test_run_no_db_returns_design():
    """db_url=None は設計結果のみ返す(ドライラン/テスト用)"""
    res = run("prop-123", db_url=None)
    assert res["proposal_id"] == "prop-123"
    assert res["business_type"] == "GENERAL"  # キーワードなし
