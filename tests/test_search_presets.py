"""jgrants_search の分野別検索プリセット (resolve_search_keywords / PRESETS) のテスト。

jGrants list API は keyword のみでしか絞れず、エリア・10/10 等はクライアント側フィルタ。
本テストは --preset が複数キーワードを横断検索するための解決ロジックを純ロジックで担保する。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "jgrants_search" / "scripts"))
from search_jgrants import PRESETS, resolve_search_keywords  # noqa: E402


def test_preset_npo_civic_contains_organization_fields():
    """Open Coral NPO の活動分野がプリセットに含まれる。"""
    kws = PRESETS["npo-civic"]
    for essential in ["子ども", "教育", "アート", "ゲーム", "AI", "地域活性化", "デジタル"]:
        assert essential in kws


def test_preset_resolution_returns_multiple_keywords():
    """--preset 指定時は複数キーワード列が返る (単一 keyword ではない)。"""
    kws = resolve_search_keywords("", preset="npo-civic")
    assert len(kws) == len(PRESETS["npo-civic"]) > 1


def test_preset_overrides_single_keyword():
    """--preset は --keyword と同時指定時もプリセット側を優先する。"""
    kws = resolve_search_keywords("子育て", preset="ngo")
    assert kws == PRESETS["ngo"]


def test_single_keyword_returns_that_keyword():
    """--keyword のみなら単一キーワード。"""
    assert resolve_search_keywords("地域", "") == ["地域"]


def test_default_sweep_keywords():
    """未指定時は主要4語のうち3語(網羅用)が返る。"""
    assert resolve_search_keywords("", "") == ["助成金", "補助金", "支援"]


def test_unknown_preset_falls_back_empty():
    """存在しないプリセット名は空リスト(→ 横断して0件、安全側)。"""
    assert resolve_search_keywords("", preset="nope") == []
