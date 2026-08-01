"""
Unit tests for ConstraintSolver (grant_expense_validator).
DB 不要 — ConstraintSolver.solve() は純粋関数に近いのでモック不要でテスト可能。
"""

import pytest
import sys
from pathlib import Path

# skills/ 配下の validate_expenses.py を直接 import するためパスを追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "grant_expense_validator" / "scripts"))

from validate_expenses import ConstraintSolver, HarnessGuardError
from constants import KEYWORD_RECATEGORY_MAP


# ---------------------------------------------------------------------------
# ヘルパー: ルール・優先度のファクトリ
# ---------------------------------------------------------------------------
def make_rule(cat: str, allowed: bool = True, max_limit=None, max_ratio=None, notes=None, evidence_quote=None):
    return {
        "category_code": cat,
        "category_label": cat,
        "allowed": allowed,
        "max_limit": max_limit,
        "max_ratio": max_ratio,
        "notes": notes,
        "evidence_quote": evidence_quote,
    }


def make_pref(cat: str, priority: int, desired: int, notes=""):
    return {
        "category_code": cat,
        "priority": priority,
        "desired_amount": desired,
        "notes": notes,
    }


# ===========================================================================
# テストケース
# ===========================================================================


class TestBasicAllocation:
    """全区分 allowed の基本配分"""

    def test_basic_allocation(self):
        rules = [
            make_rule("PERSONNEL"),
            make_rule("SYSTEM"),
        ]
        prefs = [
            make_pref("PERSONNEL", 1, 500_000),
            make_pref("SYSTEM", 2, 300_000),
        ]
        items, remaining, recs, auto_filled = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        approved = [i for i in items if i["status"] == "APPROVED"]
        assert len(approved) == 2
        assert approved[0]["allocated_amount"] == 500_000
        assert approved[1]["allocated_amount"] == 300_000
        assert remaining == 200_000
        assert not auto_filled


class TestExcludedCategory:
    """allowed=FALSE でキーワードなし → EXCLUDED"""

    def test_excluded_no_keywords(self):
        rules = [
            make_rule("TRAVEL", allowed=False, notes="旅費は対象外"),
            make_rule("PERSONNEL"),
        ]
        prefs = [
            make_pref("TRAVEL", 1, 100_000),
            make_pref("PERSONNEL", 2, 500_000),
        ]
        items, remaining, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        travel = next(i for i in items if i["category_code"] == "TRAVEL")
        assert travel["status"] == "EXCLUDED"
        assert travel["allocated_amount"] == 0


class TestSuggestedRecategorization:
    """allowed=FALSE でキーワードあり → SUGGESTED_RECATEGORIZATION"""

    def test_recategorization_detected(self):
        rules = [
            make_rule("OTHER", allowed=False, notes="雑費は対象外"),
            make_rule("SYSTEM", allowed=True),
        ]
        prefs = [
            # notes に「API」「Supabase」キーワードを含む → SYSTEM へ振替提案
            make_pref("OTHER", 1, 200_000, notes="API利用料とSupabase費用"),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        other = next(i for i in items if i["category_code"] == "OTHER")
        assert other["status"] == "SUGGESTED_RECATEGORIZATION"
        assert other["suggested_category_code"] == "SYSTEM"
        assert other["allocated_amount"] == 0


class TestRecategorizationAddsToTarget:
    """Bug 2 修正検証: 振替提案が振替先の配分に反映される"""

    def test_recategorized_amount_reflected(self):
        rules = [
            make_rule("OTHER", allowed=False, notes="雑費は対象外"),
            make_rule("SYSTEM", allowed=True),
        ]
        prefs = [
            make_pref("SYSTEM", 1, 500_000),
            make_pref("OTHER", 2, 200_000, notes="API利用料"),
        ]
        items, remaining, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )

        # SYSTEM の配分額に OTHER からの振替分が加算されているはず
        system_item = next(i for i in items if i["category_code"] == "SYSTEM" and i["status"] == "APPROVED")
        assert system_item["allocated_amount"] == 700_000  # 500k + 200k 振替

        other_item = next(i for i in items if i["category_code"] == "OTHER")
        assert other_item["status"] == "SUGGESTED_RECATEGORIZATION"
        assert other_item.get("recategorized_amount") == 200_000

    def test_recategorization_new_target(self):
        """振替先が preferences に含まれず、新規 APPROVED として追加されるケース"""
        rules = [
            make_rule("OTHER", allowed=False, notes="雑費は対象外"),
            make_rule("SYSTEM", allowed=True),
        ]
        prefs = [
            # SYSTEM を希望していないが、OTHER の振替先として SYSTEM が新規追加される
            make_pref("OTHER", 1, 300_000, notes="LLM推論コスト"),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )

        system_items = [i for i in items if i["category_code"] == "SYSTEM" and i["status"] == "APPROVED"]
        assert len(system_items) == 1
        assert system_items[0]["allocated_amount"] == 300_000


class TestNoRuleMeansNoSuggestion:
    """Bug 1 修正検証: ルール未定義の区分が振替先にならない"""

    def test_undefined_rule_not_suggested(self):
        # SYSTEM のルールを定義しない → 振替先として提案されない
        rules = [
            make_rule("OTHER", allowed=False, notes="対象外"),
            # SYSTEM ルールなし
        ]
        prefs = [
            make_pref("OTHER", 1, 200_000, notes="API利用料"),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        other = next(i for i in items if i["category_code"] == "OTHER")
        # ルール未定義なので EXCLUDED になるべき（誤って SUGGESTED_RECATEGORIZATION にならない）
        assert other["status"] == "EXCLUDED"
        assert other["suggested_category_code"] is None


class TestAutoFillRespectsMaxRatio:
    """P1 修正検証: auto-fill が比率上限を超えない"""

    def test_ratio_cap_enforced_on_auto_fill(self):
        rules = [
            make_rule("SYSTEM", allowed=True, max_ratio=0.4),  # 40% = 400k
            make_rule("PERSONNEL", allowed=True),
        ]
        prefs = [
            make_pref("SYSTEM", 1, 200_000),
            make_pref("PERSONNEL", 2, 200_000),
        ]
        items, remaining, _, auto_filled = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs, auto_fill=True
        )
        system_item = next(i for i in items if i["category_code"] == "SYSTEM")
        # auto-fill しても max_ratio 40% = 400k を超えないこと
        assert system_item["allocated_amount"] <= 400_000

        # PERSONNEL は上限なしなので残りが全て充当される
        personnel_item = next(i for i in items if i["category_code"] == "PERSONNEL")
        total = system_item["allocated_amount"] + personnel_item["allocated_amount"]
        assert total == 1_000_000
        assert auto_filled is True


class TestAutoFillFullCoverage:
    """auto-fill で coverage_rate が 1.0 (100%) になる"""

    def test_full_coverage(self):
        rules = [
            make_rule("PERSONNEL"),
            make_rule("SYSTEM"),
        ]
        prefs = [
            make_pref("PERSONNEL", 1, 300_000),
            make_pref("SYSTEM", 2, 200_000),
        ]
        items, remaining, _, auto_filled = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs, auto_fill=True
        )
        total = sum(i["allocated_amount"] for i in items)
        assert total == 1_000_000
        assert remaining == 0
        assert auto_filled is True


class TestMaxLimitAndMaxRatioCombined:
    """max_limit と max_ratio の両方が設定された場合、小さい方が適用される"""

    def test_smaller_constraint_wins(self):
        rules = [
            # max_limit = 300k, max_ratio = 0.5 → ratio_limit = 500k → effective = 300k
            make_rule("SYSTEM", max_limit=300_000, max_ratio=0.5),
        ]
        prefs = [
            make_pref("SYSTEM", 1, 1_000_000),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        system = next(i for i in items if i["category_code"] == "SYSTEM")
        assert system["allocated_amount"] == 300_000  # 定額上限が支配

    def test_ratio_is_smaller(self):
        rules = [
            # max_limit = 600k, max_ratio = 0.3 → ratio_limit = 300k → effective = 300k
            make_rule("SYSTEM", max_limit=600_000, max_ratio=0.3),
        ]
        prefs = [
            make_pref("SYSTEM", 1, 1_000_000),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        system = next(i for i in items if i["category_code"] == "SYSTEM")
        assert system["allocated_amount"] == 300_000  # 比率上限が支配


class TestDesiredExceedsRemaining:
    """希望額が残枠を超過した場合、残枠分だけ配分される"""

    def test_remaining_budget_caps_allocation(self):
        rules = [
            make_rule("PERSONNEL"),
            make_rule("SYSTEM"),
        ]
        prefs = [
            make_pref("PERSONNEL", 1, 800_000),
            make_pref("SYSTEM", 2, 500_000),  # 残枠 200k しかない
        ]
        items, remaining, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        system = next(i for i in items if i["category_code"] == "SYSTEM")
        assert system["allocated_amount"] == 200_000  # 残枠分だけ
        assert remaining == 0


class TestZeroAmountMaxRaises:
    """Bug 3 修正検証: amount_max=0 で ValueError"""

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="0 以下"):
            ConstraintSolver.solve(
                grant_amount_max=0, rules=[], preferences=[]
            )

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="0 以下"):
            ConstraintSolver.solve(
                grant_amount_max=-100, rules=[], preferences=[]
            )


class TestHarnessGuardCatchesOverflow:
    """Harness Guard: 合計超過時に HarnessGuardError"""

    def test_overflow_detected(self):
        """_verify_harness_guard が配分超過を検出して HarnessGuardError を送出する"""
        overflow_items = [
            {"allocated_amount": 600_000},
            {"allocated_amount": 500_000},
        ]
        with pytest.raises(HarnessGuardError, match="exceeds grant limit"):
            ConstraintSolver._verify_harness_guard(overflow_items, 1_000_000)

    def test_no_overflow_passes(self):
        """配分合計が上限以下なら HarnessGuardError は発生しない"""
        valid_items = [
            {"allocated_amount": 400_000},
            {"allocated_amount": 600_000},
        ]
        # 例外が発生しないことを確認（上限ちょうどは OK）
        ConstraintSolver._verify_harness_guard(valid_items, 1_000_000)


class TestKeywordRecategorizationFromConstants:
    """振替キーワードが constants.py の定義と整合していることを検証"""

    @pytest.mark.parametrize("target_cat", list(KEYWORD_RECATEGORY_MAP.keys()))
    def test_each_category_keyword_triggers_recategorization(self, target_cat):
        """各振替先カテゴリの先頭キーワードで振替が発火する"""
        first_keyword = KEYWORD_RECATEGORY_MAP[target_cat][0]
        rules = [
            make_rule("OTHER", allowed=False, notes="対象外"),
            make_rule(target_cat, allowed=True),
        ]
        prefs = [
            make_pref("OTHER", 1, 100_000, notes=f"費用: {first_keyword}"),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        other = next(i for i in items if i["category_code"] == "OTHER")
        assert other["status"] == "SUGGESTED_RECATEGORIZATION"
        assert other["suggested_category_code"] == target_cat

    def test_unknown_keyword_does_not_trigger(self):
        """キーワードリストに無い文言では振替提案されない"""
        rules = [
            make_rule("OTHER", allowed=False, notes="対象外"),
            make_rule("SYSTEM", allowed=True),
        ]
        prefs = [
            make_pref("OTHER", 1, 100_000, notes="ランチ代金"),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        other = next(i for i in items if i["category_code"] == "OTHER")
        assert other["status"] == "EXCLUDED"


# ===========================================================================
# エッジケーステスト (カバレッジ補完)
# ===========================================================================


class TestRecategorizationTargetHasLimit:
    """振替先に max_limit/max_ratio がある場合、振替額が制限される"""

    def test_target_max_limit_caps_recategorization(self):
        """振替先 SYSTEM に max_limit=150k があり、振替元 OTHER の 300k が制限される"""
        rules = [
            make_rule("OTHER", allowed=False, notes="対象外"),
            make_rule("SYSTEM", allowed=True, max_limit=150_000),
        ]
        prefs = [
            make_pref("OTHER", 1, 300_000, notes="API利用料"),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        system_items = [i for i in items if i["category_code"] == "SYSTEM" and i["status"] == "APPROVED"]
        assert len(system_items) == 1
        # max_limit=150k なので 300k ではなく 150k に制限される
        assert system_items[0]["allocated_amount"] == 150_000

    def test_target_max_ratio_caps_recategorization(self):
        """振替先 SYSTEM に max_ratio=0.1 (=100k) があり、振替額が制限される"""
        rules = [
            make_rule("OTHER", allowed=False, notes="対象外"),
            make_rule("SYSTEM", allowed=True, max_ratio=0.1),
        ]
        prefs = [
            make_pref("OTHER", 1, 500_000, notes="クラウド費用"),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        system_items = [i for i in items if i["category_code"] == "SYSTEM" and i["status"] == "APPROVED"]
        assert len(system_items) == 1
        assert system_items[0]["allocated_amount"] == 100_000


class TestMultipleRecategorizationsToSameTarget:
    """複数振替元 → 同一振替先への合算"""

    def test_two_sources_merge_to_one_target(self):
        """OTHER と TRAVEL の両方が SYSTEM へ振替提案され、合算される"""
        rules = [
            make_rule("OTHER", allowed=False, notes="対象外"),
            make_rule("TRAVEL", allowed=False, notes="旅費対象外"),
            make_rule("SYSTEM", allowed=True),
        ]
        prefs = [
            make_pref("OTHER", 1, 200_000, notes="API利用料"),
            make_pref("TRAVEL", 2, 100_000, notes="サーバー出張"),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        system_items = [i for i in items if i["category_code"] == "SYSTEM" and i["status"] == "APPROVED"]
        # 2件の振替が SYSTEM に新規APPROVED として追加される（最初の振替で作成、2番目は既存に加算）
        total_system = sum(i["allocated_amount"] for i in system_items)
        assert total_system == 300_000  # 200k + 100k


class TestAllCategoriesExcluded:
    """全カテゴリが allowed=False の場合"""

    def test_all_excluded_returns_empty_allocation(self):
        rules = [
            make_rule("PERSONNEL", allowed=False, notes="対象外"),
            make_rule("SYSTEM", allowed=False, notes="対象外"),
        ]
        prefs = [
            make_pref("PERSONNEL", 1, 500_000),
            make_pref("SYSTEM", 2, 300_000),
        ]
        items, remaining, _, auto_filled = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs
        )
        # 全項目が EXCLUDED で配分額は 0
        assert all(i["status"] == "EXCLUDED" for i in items)
        assert all(i["allocated_amount"] == 0 for i in items)
        assert remaining == 1_000_000
        assert not auto_filled


class TestEmptyPreferences:
    """preferences が空で rules のみの場合"""

    def test_no_preferences_returns_empty_items(self):
        rules = [
            make_rule("PERSONNEL"),
            make_rule("SYSTEM"),
        ]
        items, remaining, _, auto_filled = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=[]
        )
        assert items == []
        assert remaining == 1_000_000
        assert not auto_filled


class TestAutoFillAlreadyFullCoverage:
    """auto_fill=True だが既に 100% 達成済みの場合"""

    def test_auto_fill_not_applied_when_already_full(self):
        rules = [
            make_rule("PERSONNEL"),
        ]
        prefs = [
            make_pref("PERSONNEL", 1, 1_000_000),
        ]
        items, remaining, _, auto_filled = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs, auto_fill=True
        )
        personnel = next(i for i in items if i["category_code"] == "PERSONNEL")
        assert personnel["allocated_amount"] == 1_000_000
        assert remaining == 0
        # 希望額で既に100%なので auto-fill は発動しない
        assert auto_filled is False


# ===========================================================================
# Phase 2: カスタムキーワードマップ テスト
# ===========================================================================


class TestCustomKeywordMap:
    """keyword_map 引数によるキーワードマップ差し替えテスト"""

    def test_custom_keyword_triggers_recategorization(self):
        """カスタムキーワード "CUSTOM_KW" で OTHER → SYSTEM への振替が発生する"""
        rules = [
            make_rule("OTHER", allowed=False, notes="対象外"),
            make_rule("SYSTEM", allowed=True),
        ]
        prefs = [
            make_pref("OTHER", 1, 200_000, notes="CUSTOM_KW利用料"),
        ]
        custom_map = {"SYSTEM": ["CUSTOM_KW"]}
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs,
            keyword_map=custom_map
        )
        suggested = [i for i in items if i["status"] == "SUGGESTED_RECATEGORIZATION"]
        assert len(suggested) == 1
        assert suggested[0]["suggested_category_code"] == "SYSTEM"

    def test_empty_keyword_map_disables_recategorization(self):
        """空の keyword_map={} で振替が一切発生しない"""
        rules = [
            make_rule("OTHER", allowed=False, notes="対象外"),
            make_rule("SYSTEM", allowed=True),
        ]
        prefs = [
            # notes に通常なら検知されるキーワードを含む
            make_pref("OTHER", 1, 200_000, notes="API利用料 LLM"),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs,
            keyword_map={}
        )
        # 振替提案は0件、全て EXCLUDED になる
        assert all(i["status"] == "EXCLUDED" for i in items)

    def test_none_keyword_map_uses_default(self):
        """keyword_map=None (デフォルト) で constants.py の KEYWORD_RECATEGORY_MAP が使用される"""
        rules = [
            make_rule("OTHER", allowed=False, notes="対象外"),
            make_rule("SYSTEM", allowed=True),
        ]
        prefs = [
            make_pref("OTHER", 1, 200_000, notes="API利用料"),
        ]
        items, _, _, _ = ConstraintSolver.solve(
            grant_amount_max=1_000_000, rules=rules, preferences=prefs,
            keyword_map=None
        )
        suggested = [i for i in items if i["status"] == "SUGGESTED_RECATEGORIZATION"]
        assert len(suggested) == 1
        assert suggested[0]["suggested_category_code"] == "SYSTEM"
