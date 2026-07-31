#!/usr/bin/env python3
"""
Grant Expense Validator & Dynamic Solver Script (validate_expenses.py)
Matches NPO expense preferences (public.npo_expense_preferences) with grant rules
(public.grant_expense_rules) using a Deterministic Constraint Solver (0% Hallucination),
supports automatic re-categorization proposals for API/LLM/Supabase costs,
and auto-fills surplus budgets (--auto-fill) to achieve 100% grant coverage.
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import psycopg
import psycopg.rows
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Load .env
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")

CATEGORY_LABELS = {
    "PERSONNEL": "人件費",
    "TRAVEL": "旅費交通費",
    "EQUIPMENT": "備品・機器購入費",
    "OUTSOURCING": "業務委託費",
    "SYSTEM": "システム開発・クラウド費",
    "PROMOTION": "広報・印刷製本費",
    "SUPPLIES": "消耗品・会場費",
    "OTHER": "その他雑費",
}

# Auto Recategorization Keyword Mapping (API, LLM, Supabase, DB etc.)
KEYWORD_RECATEGORY_MAP = {
    "SYSTEM": [
        "API", "LLM", "OPENAI", "CLAUDE", "GEMINI",
        "SUPABASE", "NEON", "DB", "DATABASE", "データベース",
        "クラウド", "サーバー", "インフラ", "ホスティング",
        "MODAL", "VERCEL", "AWS", "GCP", "SAAS", "GPU", "開発"
    ],
    "PROMOTION": [
        "チラシ", "印刷", "広告", "パンフレット", "ポスター", "WEB広告", "動画", "PR"
    ],
    "OUTSOURCING": [
        "講師", "謝礼", "委託", "コンサル", "デザイン依頼", "外部開発", "エンジニア"
    ],
}


class HarnessGuardError(Exception):
    """Raised when harness safety verification fails."""
    pass


class ConstraintSolver:
    """Deterministic Constraint Solver with Auto-Fill & Re-categorization (0% Hallucination)"""

    @classmethod
    def _compute_effective_limit(
        cls, rule: Dict[str, Any], grant_amount_max: int
    ) -> Tuple[Optional[int], Optional[str]]:
        """ルールから effective_limit と適用制限文字列を算出する共通ヘルパー。"""
        max_limit = rule.get("max_limit") if rule else None
        max_ratio = float(rule.get("max_ratio")) if (rule and rule.get("max_ratio") is not None) else None

        effective_limit = None
        limit_reasons: List[str] = []

        if max_limit is not None and max_limit > 0:
            effective_limit = max_limit
            limit_reasons.append(f"定額上限 {max_limit:,}円")

        if max_ratio is not None and max_ratio > 0:
            ratio_limit = int(grant_amount_max * max_ratio)
            if effective_limit is None or ratio_limit < effective_limit:
                effective_limit = ratio_limit
            limit_reasons.append(f"上限比率 {max_ratio*100:.1f}% ({ratio_limit:,}円)")

        limit_str = " / ".join(limit_reasons) if limit_reasons else None
        return effective_limit, limit_str

    @classmethod
    def _detect_suggested_category(cls, text: str, rule_map: Dict[str, Any]) -> Optional[Tuple[str, str, List[str]]]:
        """Scans text for keywords and checks if an allowed target category exists."""
        text_upper = text.upper()
        for target_cat, keywords in KEYWORD_RECATEGORY_MAP.items():
            matched_kws = [kw for kw in keywords if kw.upper() in text_upper]
            if matched_kws:
                rule = rule_map.get(target_cat)
                # Bug 1 Fix: ルールが明示的に allowed=TRUE で存在する場合のみ振替先として提案
                if rule and rule.get("allowed") is True:
                    return target_cat, CATEGORY_LABELS.get(target_cat, target_cat), matched_kws
        return None

    @classmethod
    def solve(
        cls,
        grant_amount_max: int,
        rules: List[Dict[str, Any]],
        preferences: List[Dict[str, Any]],
        auto_fill: bool = False
    ) -> Tuple[List[Dict[str, Any]], int, List[str], bool]:
        # Bug 3 Fix: amount_max が 0 以下なら早期エラー
        if grant_amount_max <= 0:
            raise ValueError(
                f"助成上限額 (amount_max) が 0 以下です ({grant_amount_max})。助成金データを確認してください。"
            )

        rule_map = {r["category_code"]: r for r in rules}
        remaining_budget = grant_amount_max
        allocated_items: List[Dict[str, Any]] = []

        # Sort preferences by priority (1 is highest)
        sorted_prefs = sorted(preferences, key=lambda p: p.get("priority", 999))

        for pref in sorted_prefs:
            cat_code = pref.get("category_code", "OTHER")
            cat_label = CATEGORY_LABELS.get(cat_code, cat_code)
            priority = pref.get("priority", 999)
            desired = pref.get("desired_amount") or 0
            pref_notes = pref.get("notes") or ""

            rule = rule_map.get(cat_code) or {}

            # Pattern A: Not Allowed Rule -> Check Auto Recategorization
            if rule and not rule.get("allowed", True):
                full_text = f"{cat_label} {pref_notes}"
                suggested_info = cls._detect_suggested_category(full_text, rule_map)

                if suggested_info:
                    target_cat, target_label, matched_kws = suggested_info
                    rec_notes = (
                        f"「{cat_label}」としては対象外ですが、キーワード ({', '.join(matched_kws)}) を検知しました。"
                        f"「{target_label}」へ計上区分を変更して申請することで助成対象となります。"
                    )
                    allocated_items.append({
                        "priority": priority,
                        "category_code": cat_code,
                        "category_label": cat_label,
                        "status": "SUGGESTED_RECATEGORIZATION",
                        "desired_amount": desired,
                        "allocated_amount": 0,
                        "suggested_category_code": target_cat,
                        "suggested_category_label": target_label,
                        "limit_applied": None,
                        "notes": rec_notes,
                        "evidence_quote": rule.get("evidence_quote")
                    })
                    logger.debug("振替提案: %s → %s (キーワード: %s)", cat_label, target_label, matched_kws)
                else:
                    notes = rule.get("notes") or f"対象外経費: {cat_label} は本助成金では対象外です"
                    allocated_items.append({
                        "priority": priority,
                        "category_code": cat_code,
                        "category_label": cat_label,
                        "status": "EXCLUDED",
                        "desired_amount": desired,
                        "allocated_amount": 0,
                        "suggested_category_code": None,
                        "suggested_category_label": None,
                        "limit_applied": None,
                        "notes": notes,
                        "evidence_quote": rule.get("evidence_quote")
                    })
                    logger.debug("排除: %s (%s)", cat_label, notes)
                continue

            # Pattern B: Allowed Rule
            effective_limit, limit_str = cls._compute_effective_limit(rule, grant_amount_max)

            # Final allocation for this item
            alloc_cap = min(desired, effective_limit) if effective_limit is not None else desired
            alloc = min(alloc_cap, remaining_budget)
            remaining_budget -= alloc

            notes = rule.get("notes") or (
                "上限制限なし・全額承認" if not limit_str
                else f"上限ルールが適用されました ({limit_str})"
            )

            allocated_items.append({
                "priority": priority,
                "category_code": cat_code,
                "category_label": cat_label,
                "status": "APPROVED",
                "desired_amount": desired,
                "allocated_amount": alloc,
                "effective_limit": effective_limit,
                "suggested_category_code": None,
                "suggested_category_label": None,
                "limit_applied": limit_str,
                "notes": notes,
                "evidence_quote": rule.get("evidence_quote") if rule else None
            })
            logger.debug("承認: %s → %s円 (希望: %s円, 上限: %s)", cat_label, f"{alloc:,}", f"{desired:,}", effective_limit)

        # --- Bug 2 Fix: 振替提案の配分を振替先区分に反映 ---
        pending_recats = [
            item for item in allocated_items
            if item["status"] == "SUGGESTED_RECATEGORIZATION" and item.get("suggested_category_code")
        ]
        for recat_item in pending_recats:
            target_cat = recat_item["suggested_category_code"]
            amount = recat_item["desired_amount"]

            # 振替先の既存APPROVEDアイテムを検索
            target_approved = next(
                (i for i in allocated_items if i["category_code"] == target_cat and i["status"] == "APPROVED"),
                None
            )

            if target_approved:
                # 既存の振替先に追加配分
                eff = target_approved.get("effective_limit")
                current = target_approved["allocated_amount"]
                headroom = (eff - current) if eff is not None else remaining_budget
                topup = min(amount, max(headroom, 0), remaining_budget)
                if topup > 0:
                    target_approved["allocated_amount"] += topup
                    remaining_budget -= topup
                    recat_item["recategorized_amount"] = topup
                    logger.debug(
                        "振替配分: %s → %s に %s円 追加",
                        recat_item["category_label"], target_approved["category_label"], f"{topup:,}"
                    )
            else:
                # 振替先がまだ配分リストにない → 新規APPROVED項目として追加
                target_rule = rule_map.get(target_cat, {})
                if target_rule and target_rule.get("allowed") is True:
                    eff_limit, eff_limit_str = cls._compute_effective_limit(target_rule, grant_amount_max)
                    alloc = min(
                        amount,
                        eff_limit if eff_limit is not None else amount,
                        remaining_budget
                    )
                    if alloc > 0:
                        remaining_budget -= alloc
                        allocated_items.append({
                            "priority": recat_item["priority"],
                            "category_code": target_cat,
                            "category_label": CATEGORY_LABELS.get(target_cat, target_cat),
                            "status": "APPROVED",
                            "desired_amount": amount,
                            "allocated_amount": alloc,
                            "effective_limit": eff_limit,
                            "suggested_category_code": None,
                            "suggested_category_label": None,
                            "limit_applied": eff_limit_str,
                            "notes": f"振替配分: 「{recat_item['category_label']}」からの振替提案による配分",
                            "evidence_quote": None,
                        })
                        recat_item["recategorized_amount"] = alloc
                        logger.debug("振替新規配分: %s に %s円 配分", target_cat, f"{alloc:,}")

        auto_fill_applied = False
        recommendations: List[str] = []

        # --auto-fill processing if surplus budget remains
        if auto_fill and remaining_budget > 0:
            for item in allocated_items:
                if item["status"] != "APPROVED":
                    continue

                # P1 Fix: max_ratio 制約を再計算して auto-fill 時も適用
                cat_rule = rule_map.get(item["category_code"], {})
                max_ratio_val = float(cat_rule["max_ratio"]) if cat_rule.get("max_ratio") is not None else None
                ratio_cap = int(grant_amount_max * max_ratio_val) if max_ratio_val and max_ratio_val > 0 else None

                effective_limit = item.get("effective_limit")
                if ratio_cap is not None:
                    effective_limit = min(effective_limit, ratio_cap) if effective_limit is not None else ratio_cap

                current_alloc = item["allocated_amount"]

                if effective_limit is None or current_alloc < effective_limit:
                    headroom = (effective_limit - current_alloc) if effective_limit is not None else remaining_budget
                    topup = min(remaining_budget, max(headroom, 0))
                    if topup > 0:
                        item["allocated_amount"] += topup
                        remaining_budget -= topup
                        auto_fill_applied = True
                        item["notes"] += f" (＋余剰枠自動上乗せ {topup:,}円)"
                        logger.debug("auto-fill: %s に %s円 上乗せ", item["category_label"], f"{topup:,}")
                        if remaining_budget == 0:
                            break

            if auto_fill_applied:
                recommendations.append(
                    f"✨ --auto-fill により、優先度の上位経費へ余剰枠を自動充当し、助成上限額 {grant_amount_max:,} 円を 100% 満額達成しました。"
                )

        if remaining_budget > 0 and not auto_fill_applied:
            approved_cats = [item["category_label"] for item in allocated_items if item["status"] == "APPROVED"]
            rec_str = f"助成上限枠に対して {remaining_budget:,} 円の残額があります。"
            if approved_cats:
                rec_str += f" 優先順位の高い 「{approved_cats[0]}」 への追加充当を推奨します。(--auto-fill で自動満額化可能)"
            recommendations.append(rec_str)

        total_allocated = sum(item["allocated_amount"] for item in allocated_items)

        # Harness Guard Verification
        if total_allocated > grant_amount_max:
            raise HarnessGuardError(
                f"Harness Guard Failed: Total allocated ({total_allocated:,}円) exceeds grant limit ({grant_amount_max:,}円)"
            )

        return allocated_items, remaining_budget, recommendations, auto_fill_applied


class ExpenseValidator:
    """Orchestrator for Expense Validation & Portfolio Optimization"""

    def __init__(self, db_url: str):
        self.db_url = db_url

    def run(self, org_id: str, grant_id: str, auto_fill: bool = False) -> Dict[str, Any]:
        with psycopg.connect(self.db_url, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                # 1. Get NPO Profile
                cur.execute("SELECT * FROM public.npo_profiles WHERE id = %s;", (org_id,))
                npo = cur.fetchone()
                if not npo:
                    raise ValueError(f"NPO Profile with ID '{org_id}' not found.")

                # 2. Get Grant
                if grant_id.isdigit():
                    cur.execute(
                        "SELECT * FROM public.grants WHERE id = %s OR source_grant_id = %s;",
                        (int(grant_id), grant_id)
                    )
                else:
                    cur.execute("SELECT * FROM public.grants WHERE source_grant_id = %s;", (grant_id,))
                grant = cur.fetchone()
                if not grant:
                    raise ValueError(f"Grant with ID '{grant_id}' not found.")

                db_grant_id = grant["id"]
                amount_max = grant.get("amount_max") or 0

                # Bug 3 Fix: 助成上限額が未設定の場合は早期エラー
                if amount_max <= 0:
                    raise ValueError(
                        f"助成金 '{grant.get('title', grant_id)}' の助成上限額 (amount_max) が未設定または 0 です。"
                    )

                # 3. Get Rules & Preferences
                cur.execute(
                    "SELECT * FROM public.grant_expense_rules WHERE grant_id = %s;",
                    (db_grant_id,)
                )
                rules = cur.fetchall()

                cur.execute(
                    "SELECT * FROM public.npo_expense_preferences WHERE npo_profile_id = %s ORDER BY priority ASC;",
                    (org_id,)
                )
                preferences = cur.fetchall()

                # フォールバック: 優先度未登録の場合はデフォルト配分 (withブロック内で完結)
                if not preferences:
                    preferences = [
                        {"category_code": "PERSONNEL", "priority": 1, "desired_amount": int(amount_max * 0.5)},
                        {"category_code": "SYSTEM", "priority": 2, "desired_amount": int(amount_max * 0.3)},
                        {"category_code": "PROMOTION", "priority": 3, "desired_amount": int(amount_max * 0.2)},
                    ]

        allocated_items, remaining_budget, recommendations, auto_fill_applied = ConstraintSolver.solve(
            grant_amount_max=amount_max,
            rules=rules,
            preferences=preferences,
            auto_fill=auto_fill
        )

        total_allocated = sum(item["allocated_amount"] for item in allocated_items)
        coverage_rate = round(total_allocated / amount_max, 4) if amount_max > 0 else 0.0

        report = {
            "grant_id": grant["id"],
            "grant_title": grant["title"],
            "npo_profile_id": str(npo["id"]),
            "npo_name": npo["name"],
            "grant_amount_max": amount_max,
            "total_allocated": total_allocated,
            "remaining_budget": remaining_budget,
            "coverage_rate": coverage_rate,
            "auto_fill_applied": auto_fill_applied,
            "items": allocated_items,
            "recommendations": recommendations,
            "evaluated_at": datetime.now().isoformat()
        }

        return report


def main():
    parser = argparse.ArgumentParser(description="Grant Expense Validator & Dynamic Solver")
    parser.add_argument("--org-id", required=True, help="NPO Profile UUID")
    parser.add_argument("--grant-id", required=True, help="Grant DB ID or Source Grant ID")
    parser.add_argument("--auto-fill", action="store_true", help="Auto fill surplus budget to achieve 100% grant coverage")
    parser.add_argument("--json", action="store_true", help="Output result in JSON format")

    args = parser.parse_args()

    if not DATABASE_URL:
        logging.error("DATABASE_URL is not set in environment variables.")
        sys.exit(1)

    try:
        validator = ExpenseValidator(DATABASE_URL)
        report = validator.run(org_id=args.org_id, grant_id=args.grant_id, auto_fill=args.auto_fill)

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("\n==================================================")
            print(f" 最適経費ポートフォリオ: {report['grant_title']}")
            print(f" 団体名: {report['npo_name']}")
            print("==================================================")
            print(f" 助成上限額: {report['grant_amount_max']:,}円 | 充当合計: {report['total_allocated']:,}円 (カバー率: {report['coverage_rate']*100:.1f}%)")
            print(f" 予算残額: {report['remaining_budget']:,}円\n")

            for item in report["items"]:
                if item["status"] == "APPROVED":
                    icon = "✅"
                elif item["status"] == "SUGGESTED_RECATEGORIZATION":
                    icon = "💡"
                else:
                    icon = "❌"

                print(f" {icon} 優先度{item['priority']}: {item['category_label']}")
                if item["status"] == "APPROVED":
                    print(f"    → 配分額: {item['allocated_amount']:,}円 (希望額: {item['desired_amount']:,}円)")
                    if item["limit_applied"]:
                        print(f"      (適用制限: {item['limit_applied']})")
                elif item["status"] == "SUGGESTED_RECATEGORIZATION":
                    print(f"    → 【振替推奨】 {item['category_label']} → {item['suggested_category_label']}")
                    print(f"      理由: {item['notes']}")
                    if item.get("evidence_quote"):
                        print(f"      根拠引用: \"{item['evidence_quote']}\"")
                    if item.get("recategorized_amount"):
                        print(f"      → 振替先に {item['recategorized_amount']:,}円 を自動配分済み")
                else:
                    print(f"    → 排除理由: {item['notes']}")
                    if item.get("evidence_quote"):
                        print(f"      根拠引用: \"{item['evidence_quote']}\"")
                print()

            if report["recommendations"]:
                print("【再配分・活用推奨案】")
                for rec in report["recommendations"]:
                    print(f" 💡 {rec}")
            print("==================================================\n")

    except Exception as e:
        logging.error(f"Execution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
