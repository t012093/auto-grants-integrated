"""sync_grants_cron の CLOSED 化安全弁(連続不在グレース)の回帰テスト。

背景: jGrants list API がページ上限で打ち切られる/一時的に不完全な場合に、
「1回のスイープで見えなかった」だけで募集中助成金を誤って CLOSED 化すると
データ損失になる。compute_closed_candidates がこの種の誤CLOSEDを防ぐことを検証する。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "jgrants_search" / "scripts"))
from sync_grants_cron import compute_closed_candidates  # noqa: E402


def test_first_absence_does_not_close():
    """1回目の不在は CLOSED 化せず、カウントを進めるだけ。"""
    db = {"1", "2", "3"}
    api = {"1", "2"}  # "3" が今回見えなかった
    to_close, new_state = compute_closed_candidates(db, api, {}, grace=3, healthy=True)
    assert to_close == set()
    assert new_state == {"3": 1}


def test_grace_reached_closes_and_resets():
    """連続 3 回(grace)の不在観測で初めて CLOSED 化し、カウンタをリセット。"""
    db = {"1", "2", "3"}
    api = {"1", "2"}
    to_close, new_state = compute_closed_candidates(
        db, api, {"3": 2}, grace=3, healthy=True
    )
    assert to_close == {"3"}
    assert new_state == {"3": 0}


def test_reappearance_resets_counter():
    """不在後に API へ再出現した ID は、カウンタがリセットされ CLOSED 化しない。"""
    db = {"1", "2", "3"}
    api = {"1", "2", "3"}  # "3" が再出現
    to_close, new_state = compute_closed_candidates(
        db, api, {"3": 2}, grace=3, healthy=True
    )
    assert to_close == set()
    assert "3" not in new_state


def test_unhealthy_sweep_never_closes():
    """スイープ不健全(healthy=False)のときは一切 CLOSED 化しない。"""
    db = {"1", "2", "3"}
    api = {"1"}  # 不完全取得を模擬
    to_close, new_state = compute_closed_candidates(
        db, api, {"3": 2}, grace=3, healthy=False
    )
    assert to_close == set()
    # state はそのまま維持(今回の観測を進めない)
    assert new_state == {"3": 2}


def test_grace_one_closes_immediately():
    """grace=1 なら初回不在で CLOSED 化(旧挙動と等価)。"""
    db = {"1", "2", "3"}
    api = {"1", "2"}
    to_close, new_state = compute_closed_candidates(db, api, {}, grace=1, healthy=True)
    assert to_close == {"3"}
    assert new_state == {"3": 0}
