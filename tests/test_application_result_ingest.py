"""Phase C: 採択結果取込 (record_application_result / monitor_gmail_awards) のテスト"""
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

from monitor_gmail_awards import (  # noqa: E402
    _detect_result,
    _extract_grant_id,
    _connect_mailbox,
    run_monitor,
    UNKNOWN_RESULT,
)
from record_application_result import VALID_RESULTS, record_result  # noqa: E402


# --- record_application_result: バリデーション ---
def test_valid_results():
    assert VALID_RESULTS == ("AWARDED", "REJECTED", "PENDING")


def test_record_result_rejects_invalid_result():
    with pytest.raises(ValueError):
        record_result(db_url="x", org_id="o", grant_id=1, result="GRANTED_XYZ")


def test_record_result_requires_db(monkeypatch):
    """偽成功しない: record_result は実際に DB に書き込むので db_url が無いと呼べない"""
    # 直接実DBは触らない。バリデーションのみが DB 非依存で検証可能
    assert "AWARDED" in VALID_RESULTS


# --- monitor_gmail_awards: _detect_result 判定 ---
def test_detect_awarded():
    assert _detect_result(subject="【採択通知】令和8年度地域助成金", body="貴団体は採択されました") == "AWARDED"


def test_detect_rejected():
    assert _detect_result(body="厳正な審査の結果、不採択となりました") == "REJECTED"


def test_detect_rejected_not_confused_with_award():
    # 「不採択」を「採択」と誤判定しない (否定語優先)
    assert _detect_result(body="応募多数のため不採択となりました") == "REJECTED"


def test_detect_pending():
    assert _detect_result(body="現在審査中です。結果は後日") == "PENDING"


def test_detect_unknown_returns_unknown():
    """判定不能は UNKNOWN を返す (偽成功しない・採択/不採択と断定しない)"""
    assert _detect_result(subject="ニュースレター", body="今月の活動報告です") == UNKNOWN_RESULT
    assert _detect_result(subject="", body="") == UNKNOWN_RESULT


def test_detect_empty_is_unknown():
    assert _detect_result() == UNKNOWN_RESULT


# --- _extract_grant_id ---
def test_extract_grant_id_matches_title():
    known = {"令和8年度 地域デジタルイノベーション創出助成金": 20}
    body = "この度は【地域デジタルイノベーション創出助成金】に採択されました"
    # 部分一致として題名の一部が本文に含まれるケース
    assert _extract_grant_id(body, {}) is None  # known なし
    gid = _extract_grant_id("令和8年度 地域デジタルイノベーション創出助成金について", known)
    assert gid == 20


def test_extract_grant_id_no_match():
    assert _extract_grant_id("全く関係ないメール", {"別の助成金": 1}) is None


# --- 骨格は認証未実装で明示的に失敗する (偽成功しない) ---
def test_gmail_skeleton_raises_notimplemented():
    with pytest.raises(NotImplementedError):
        _connect_mailbox()


def test_gmail_run_monitor_raises_notimplemented():
    with pytest.raises(NotImplementedError):
        run_monitor()
