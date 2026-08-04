#!/usr/bin/env python3
"""
6-Gate Grant Eligibility Checker
Scans npo_profiles & grants data from Neon DB, performs full 6-gate eligibility evaluation,
and saves/updates the result in public.alerts using PostgreSQL ON CONFLICT.
"""

import os
import sys
import json
import logging
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import psycopg
import psycopg.rows
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Load .env
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")

# 47都道府県リスト (前方一致正規化用)
PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]


def normalize_prefecture(text: str) -> str:
    """テキストから都道府県名を前方一致で抽出。見つからなければ原文を返す。"""
    for p in PREFECTURES:
        if text.startswith(p):
            return p
    return text


def area_match(grant_area: str, location: str) -> bool:
    """都道府県レベルで地域が一致するか判定。'京都' in '東京都' の誤判定を防止。"""
    if "全国" in location:
        return True
    return normalize_prefecture(grant_area) == normalize_prefecture(location)


# ---------------------------------------------------------------------------
# GateResult dataclass — 全ゲートの統一出力構造
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    gate_code: str           # "GATE_1" 〜 "GATE_6"
    gate_name: str           # 日本語名
    passed: bool             # True / False
    status: str              # "PASS" / "WARN" / "FAIL" / "SKIP"
    score: int = 100         # 0-100
    details: Dict[str, Any] = field(default_factory=dict)
    failed_items: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Gate 1: 基本ルール判定 (法人格 / 実績年数 / 公募ステータス)
# ---------------------------------------------------------------------------

class Gate1BasicRuleEvaluator:
    """Gate 1: 法人格一致・実績年数・公募ステータスの確定ルール判定"""

    @staticmethod
    def evaluate(npo: Dict[str, Any], grant: Dict[str, Any]) -> GateResult:
        details = {}
        failed_items = []

        # 1. 法人格一致 (organization_type)
        eligible_types = grant.get("eligible_org_types") or ["NPO_CORPORATION", "GENERAL_INC", "UNINCORPORATED"]
        npo_type = npo.get("organization_type") or "NPO_CORPORATION"
        type_pass = npo_type in eligible_types
        details["organization_type"] = {
            "pass": type_pass,
            "reason": f"団体型 '{npo_type}' は対象枠 {eligible_types} に{'含まれます' if type_pass else '含まれません'}"
        }
        if not type_pass:
            failed_items.append("organization_type")

        # 2. 実績年数 (years_active)
        min_years = grant.get("min_years_active") or 0
        est_year = npo.get("establishment_year")
        current_year = datetime.now().year
        active_years = (current_year - est_year) if est_year else 0
        years_pass = active_years >= min_years
        details["years_active"] = {
            "pass": years_pass,
            "reason": f"活動実績 {active_years}年 (必要年数: {min_years}年)"
        }
        if not years_pass:
            failed_items.append("years_active")

        # 3. 公募ステータス (grant_status)
        status = grant.get("status") or "OPEN"
        deadline = grant.get("deadline")
        is_open = status == "OPEN"
        deadline_valid = True
        if deadline:
            if isinstance(deadline, str):
                try:
                    deadline = datetime.strptime(deadline[:10], "%Y-%m-%d").date()
                except ValueError:
                    # パース失敗時は安全側に倒して無効(False)とする
                    deadline_valid = False
            elif isinstance(deadline, datetime):
                deadline = deadline.date()
            if isinstance(deadline, date):
                deadline_valid = deadline >= date.today()
        status_pass = is_open and deadline_valid
        details["grant_status"] = {
            "pass": status_pass,
            "reason": f"ステータス '{status}' / 締切 '{deadline or '未設定'}'"
        }
        if not status_pass:
            failed_items.append("grant_status")

        all_pass = len(failed_items) == 0
        return GateResult(
            gate_code="GATE_1",
            gate_name="基本ルール判定",
            passed=all_pass,
            status="PASS" if all_pass else "FAIL",
            score=100 if all_pass else 0,
            details=details,
            failed_items=failed_items,
            reason="基本要件をすべて満たしています" if all_pass else f"不合格項目: {', '.join(failed_items)}"
        )


# ---------------------------------------------------------------------------
# Gate 2: 拠点要件 (都道府県前方一致マッチング)
# ---------------------------------------------------------------------------

class Gate2LocationEvaluator:
    """Gate 2: 対象地域の拠点・活動地域適合判定"""

    @staticmethod
    def evaluate(npo: Dict[str, Any], grant: Dict[str, Any]) -> GateResult:
        grant_area = grant.get("target_area") or "全国"
        req_type = grant.get("location_requirement_type") or "BRANCH_ALLOWED"

        hq_loc = npo.get("headquarter_location") or ""
        branches = npo.get("branch_locations") or []
        activities = npo.get("activity_areas") or []
        legacy_loc = npo.get("location") or "全国"

        if grant_area == "全国":
            area_pass = True
            reason = f"公募エリア '{grant_area}' (全国開放枠)"
        elif req_type == "HEADQUARTER_ONLY":
            check_target = hq_loc or legacy_loc
            area_pass = area_match(grant_area, check_target)
            reason = f"公募エリア '{grant_area}' (本店限定要件) vs 本店拠点 '{check_target}'"
        elif req_type == "ACTIVITY_AREA_ONLY":
            check_list = activities or ([legacy_loc] if legacy_loc else [])
            area_pass = any(area_match(grant_area, a) for a in check_list)
            reason = f"公募エリア '{grant_area}' (事業実施地要件) vs 活動地域 {check_list}"
        else:  # BRANCH_ALLOWED (デフォルト)
            check_list = ([hq_loc] if hq_loc else []) + list(branches)
            if not check_list and legacy_loc:
                check_list = [legacy_loc]
            area_pass = any(area_match(grant_area, c) for c in check_list)
            reason = f"公募エリア '{grant_area}' (支店認容要件) vs 本店・支店拠点 {check_list}"

        return GateResult(
            gate_code="GATE_2",
            gate_name="拠点要件",
            passed=area_pass,
            status="PASS" if area_pass else "FAIL",
            score=100 if area_pass else 0,
            details={"target_area": {"pass": area_pass, "reason": reason}},
            failed_items=[] if area_pass else ["target_area"],
            reason=reason
        )


# ---------------------------------------------------------------------------
# Gate 3: 予算規模 (助成上限 <= 年予算 50%)
# ---------------------------------------------------------------------------

class Gate3BudgetEvaluator:
    """Gate 3: 予算規模の整合性判定"""

    @staticmethod
    def evaluate(npo: Dict[str, Any], grant: Dict[str, Any]) -> GateResult:
        max_amount = grant.get("amount_max") or 0
        annual_budget = npo.get("annual_budget") or 0

        if max_amount > 0 and annual_budget > 0:
            budget_ratio = max_amount / annual_budget
            budget_pass = budget_ratio <= 0.50
            reason = f"助成上限 {max_amount:,}円 / 前年予算 {annual_budget:,}円 (比率: {budget_ratio*100:.1f}% <= 50%上限)"
        else:
            budget_pass = True
            reason = "予算要件制限なし"

        return GateResult(
            gate_code="GATE_3",
            gate_name="予算規模",
            passed=budget_pass,
            status="PASS" if budget_pass else "FAIL",
            score=100 if budget_pass else 0,
            details={"budget_ratio": {"pass": budget_pass, "reason": reason}},
            failed_items=[] if budget_pass else ["budget_ratio"],
            reason=reason
        )


# ---------------------------------------------------------------------------
# Gate 4: セマンティック適合度 (8軸 pgvector + キーワード)
# ---------------------------------------------------------------------------

class Gate4SemanticEvaluator:
    """Gate 4: 8-Item Semantic Alignment (pgvector Cosine Similarity) & Qualitative Rules"""

    # pgvector クエリ失敗時・データ未投入時のフォールバックスコア
    FALLBACK_SCORE = 75

    @staticmethod
    def _get_vector_similarity(cur: Any, npo_id: str, grant_id: int, chunk_type: str) -> Tuple[int, Optional[str]]:
        """NPO チャンクと助成金チャンクの最近傍コサイン類似度を返す。

        NPO 側は chunk_type フィルタで 1 行に絞られ、助成金側は
        サブクエリ内で最近傍 1 件のみ取得するため直積は発生しない。
        """
        try:
            cur.execute(
                """
                SELECT sub.content, sub.similarity
                FROM public.npo_knowledge_chunks nc,
                LATERAL (
                    SELECT kc.content,
                           1 - (kc.embedding <=> nc.embedding) AS similarity
                    FROM public.knowledge_chunks kc
                    WHERE kc.grant_id = %s
                    ORDER BY kc.embedding <=> nc.embedding
                    LIMIT 1
                ) sub
                WHERE nc.npo_profile_id = %s AND nc.chunk_type = %s;
                """,
                (grant_id, npo_id, chunk_type)
            )
            row = cur.fetchone()
            if row and row.get("similarity") is not None:
                sim = float(row["similarity"])
                score = int(min(max(sim, 0.0), 1.0) * 100)
                return score, row.get("content")
        except Exception as e:
            logging.warning(f"pgvector query fallback for {chunk_type}: {e}")
        return Gate4SemanticEvaluator.FALLBACK_SCORE, None

    @classmethod
    def evaluate(cls, cur: Any, npo: Dict[str, Any], grant: Dict[str, Any]) -> GateResult:
        detail_text = grant.get("detail_text") or ""
        title = grant.get("title") or ""
        full_grant_text = f"{title} {detail_text}"

        org_id = str(npo.get("id"))
        grant_id = grant.get("id")

        scores = {}
        evidence_quotes = []

        # 10. 活動分野適合度 (pgvector Cosine Similarity: ACTIVITY_TAGS)
        act_score, act_quote = cls._get_vector_similarity(cur, org_id, grant_id, "ACTIVITY_TAGS")
        if act_quote is None:
            tags = npo.get("activity_tags") or []
            tag_hits = sum(1 for tag in tags if tag in full_grant_text)
            act_score = min(70 + tag_hits * 15, 100)
        else:
            evidence_quotes.append(act_quote)
        scores["activity_category"] = act_score

        # 11. ターゲット層適合度 (pgvector Cosine Similarity: TARGET_AUDIENCE)
        aud_score, aud_quote = cls._get_vector_similarity(cur, org_id, grant_id, "TARGET_AUDIENCE")
        if aud_quote is None:
            audiences = npo.get("target_audience") or []
            aud_hits = sum(1 for aud in audiences if aud in full_grant_text)
            aud_score = min(75 + aud_hits * 15, 100)
        else:
            if aud_quote not in evidence_quotes:
                evidence_quotes.append(aud_quote)
        scores["target_audience"] = aud_score

        # 12. 事業目的適合度 (pgvector Cosine Similarity: DESCRIPTION)
        desc_score, desc_quote = cls._get_vector_similarity(cur, org_id, grant_id, "DESCRIPTION")
        if desc_quote is None:
            desc = npo.get("description") or ""
            desc_score = 85 if len(desc) > 20 else 70
        else:
            if desc_quote not in evidence_quotes:
                evidence_quotes.append(desc_quote)
        scores["purpose_match"] = desc_score

        # 13. 連携・体制要求
        partnership_keywords = ["連携", "協働", "パートナー", "地域住民", "他団体", "ネットワーク"]
        has_partner = any(kw in full_grant_text for kw in partnership_keywords)
        scores["partnership_req"] = 90 if has_partner else 75

        # 14. 先進性・新規性要求
        uniqueness_keywords = ["新規", "先進", "モデル", "革新", "挑戦", "パイオニア", "実証"]
        has_uniqueness = any(kw in full_grant_text for kw in uniqueness_keywords)
        scores["uniqueness_req"] = 90 if has_uniqueness else 80

        # 15. 自己負担・補助率整合
        is_10_10 = grant.get("is_rate_10_10", False)
        scores["cost_burden"] = 100 if is_10_10 else 80

        # 16. 概算払い・資金繰り適合
        is_advance = grant.get("is_advance_payment", False)
        scores["advance_payment"] = 100 if is_advance else 75

        # 17. 反社排除・コンプライアンス要件
        scores["compliance"] = 100

        # Substring Match Guard Check
        valid_quotes = []
        for q in evidence_quotes:
            if q in detail_text or q in title:
                valid_quotes.append(q)
            elif len(q) >= 20 and any(q[i:i+20] in detail_text for i in range(len(q) - 19)):
                valid_quotes.append(q)
        if not valid_quotes and detail_text:
            snippet = detail_text[:60].strip()
            if snippet:
                valid_quotes.append(snippet)

        avg_score = int(sum(scores.values()) / len(scores)) if scores else 80

        return GateResult(
            gate_code="GATE_4",
            gate_name="セマンティック適合度",
            passed=True,  # Gate 4 は常に pass (スコア貢献)
            status="PASS",
            score=avg_score,
            details={
                "criteria_scores": scores,
                "evidence_quotes": valid_quotes
            }
        )


# ---------------------------------------------------------------------------
# Gate 5: 特定要件 RAG (正方向ベクトル検索)
# ---------------------------------------------------------------------------

class Gate5RequirementRAGEvaluator:
    """Gate 5: 特定要件 RAG — 助成金要件文 → NPO実績チャンクへの正方向ベクトル検索"""

    SIMILARITY_PASS = 0.70
    SIMILARITY_WARN = 0.50

    @classmethod
    def evaluate(cls, cur: Any, npo: Dict[str, Any], grant: Dict[str, Any], embedder: Any) -> GateResult:
        requirement_sentences = grant.get("requirement_sentences") or []
        if not requirement_sentences:
            return GateResult(
                gate_code="GATE_5",
                gate_name="特定要件 RAG",
                passed=True,
                status="SKIP",
                score=100,
                reason="要件文未抽出 (スキップ)"
            )

        npo_id = str(npo["id"])

        # Guard: NPO チャンクの存在確認
        cur.execute("SELECT COUNT(*) AS cnt FROM public.npo_knowledge_chunks WHERE npo_profile_id = %s;", (npo_id,))
        row = cur.fetchone()
        count = row["cnt"] if isinstance(row, dict) else row[0]
        if count == 0:
            return GateResult(
                gate_code="GATE_5",
                gate_name="特定要件 RAG",
                passed=True,  # WARN は打切りではない
                status="WARN",
                score=50,
                reason="NPO実績ベクトルデータ未登録",
                details={"items": [
                    {
                        "grant_requirement": req,
                        "npo_matched_evidence": "実績データなし",
                        "similarity_score": 0.0,
                        "status": "WARN",
                        "explanation": f"要件『{req}』に対する団体実績データが登録されていません。",
                        "user_advice": "団体プロファイルの実績・活動情報を登録して再判定してください。"
                    }
                    for req in requirement_sentences
                ]}
            )

        items = []
        has_fail = False
        has_warn = False

        for req in requirement_sentences:
            # 要件文を BGE-M3 でベクトル化 (正規化あり)
            req_vec = embedder.encode([req], normalize_embeddings=True)[0]
            vec_str = str(req_vec.tolist())

            # NPO 実績チャンクに対して正方向コサイン検索
            cur.execute(
                """
                SELECT content, 1 - (embedding <=> %s::vector) AS similarity
                FROM public.npo_knowledge_chunks
                WHERE npo_profile_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT 1;
                """,
                (vec_str, npo_id, vec_str)
            )

            match_row = cur.fetchone()
            if match_row:
                sim = float(match_row["similarity"] if isinstance(match_row, dict) else match_row[1])
                evidence = (match_row["content"] if isinstance(match_row, dict) else match_row[0]) or ""
            else:
                sim = 0.0
                evidence = ""

            if sim >= cls.SIMILARITY_PASS:
                item_status = "PASS"
            elif sim >= cls.SIMILARITY_WARN:
                item_status = "WARN"
                has_warn = True
            else:
                item_status = "FAIL"
                has_fail = True

            explanation, advice = cls._generate_explanation(req, evidence, sim, item_status)

            items.append({
                "grant_requirement": req,
                "npo_matched_evidence": evidence[:100],
                "similarity_score": round(sim, 4),
                "status": item_status,
                "explanation": explanation,
                "user_advice": advice
            })

        if has_fail:
            overall_status = "FAIL"
        elif has_warn:
            overall_status = "WARN"
        else:
            overall_status = "PASS"

        return GateResult(
            gate_code="GATE_5",
            gate_name="特定要件 RAG",
            passed=(overall_status != "FAIL"),
            status=overall_status,
            score=0 if has_fail else (50 if has_warn else 100),
            details={"items": items},
            failed_items=[it["grant_requirement"][:30] for it in items if it["status"] == "FAIL"],
            reason=f"要件適合判定: {overall_status}"
        )

    @staticmethod
    def _generate_explanation(req: str, evidence: str, sim: float, status: str) -> Tuple[str, str]:
        if status == "FAIL":
            explanation = (
                f"公募要件『{req}』に対する十分な関連実績が"
                f"団体データ内に確認できませんでした (類似度: {sim:.2f})。"
            )
        elif status == "WARN":
            explanation = (
                f"公募要件『{req}』に関連する記述が見つかりましたが、"
                f"十分な適合とは判定できません (類似度: {sim:.2f})。"
            )
        else:
            explanation = f"公募要件『{req}』に適合する実績が確認できました (類似度: {sim:.2f})。"

        advice = "該当する実績がある場合は、団体プロファイルの実績情報にテキストを追記して再判定してください。"
        return explanation, advice


# ---------------------------------------------------------------------------
# Gate 6: 書類準備率
# ---------------------------------------------------------------------------

class Gate6DocumentEvaluator:
    """Gate 6: 提出書類の準備状況チェック"""

    @staticmethod
    def evaluate(npo: Dict[str, Any], grant: Dict[str, Any]) -> GateResult:
        raw_required = grant.get("required_documents")
        required_docs = set(raw_required) if raw_required is not None else set()
        prepared_docs = set(npo.get("prepared_documents") or [])

        # If required_documents is explicit empty list, no documents are required
        if raw_required is None:
            required_docs = {"ARTICLES", "FINANCIAL_REPORT", "BOARD_LIST", "REGISTRY_CERTIFICATE"}

        missing_docs = list(required_docs - prepared_docs)
        prepared_matched = list(required_docs & prepared_docs)

        score = int((len(prepared_matched) / len(required_docs)) * 100) if required_docs else 100

        return GateResult(
            gate_code="GATE_6",
            gate_name="書類準備率",
            passed=True,  # Gate 6 は常に pass (スコア貢献)
            status="PASS" if score == 100 else ("WARN" if score >= 50 else "FAIL"),
            score=score,
            details={
                "required": sorted(list(required_docs)),
                "prepared": sorted(prepared_matched),
                "missing": sorted(missing_docs)
            },
            failed_items=sorted(missing_docs),
            reason=f"書類準備率 {score}% ({len(prepared_matched)}/{len(required_docs)})"
        )


# ---------------------------------------------------------------------------
# 後方互換エイリアス (既存テスト・外部参照用)
# ---------------------------------------------------------------------------

class Stage1RuleEvaluator:
    """後方互換: Gate 1/2/3 を統合して旧 Stage 1 インターフェースを提供"""

    @staticmethod
    def evaluate(npo: Dict[str, Any], grant: Dict[str, Any]) -> Dict[str, Any]:
        g1 = Gate1BasicRuleEvaluator.evaluate(npo, grant)
        g2 = Gate2LocationEvaluator.evaluate(npo, grant)
        g3 = Gate3BudgetEvaluator.evaluate(npo, grant)

        all_pass = g1.passed and g2.passed and g3.passed
        details = {}
        details.update(g1.details)
        details.update(g2.details)
        details.update(g3.details)

        return {"all_pass": all_pass, "details": details}


class Stage2DocumentMatcher:
    """後方互換: Gate 6 を旧 Stage 2 インターフェースで提供"""

    @staticmethod
    def evaluate(npo: Dict[str, Any], grant: Dict[str, Any]) -> Dict[str, Any]:
        g6 = Gate6DocumentEvaluator.evaluate(npo, grant)
        return {
            "score": g6.score,
            "required": g6.details.get("required", []),
            "prepared": g6.details.get("prepared", []),
            "missing": g6.details.get("missing", [])
        }


class Stage3SemanticEvaluator:
    """後方互換: Gate 4 を旧 Stage 3 インターフェースで提供"""

    FALLBACK_SCORE = Gate4SemanticEvaluator.FALLBACK_SCORE

    @staticmethod
    def _get_vector_similarity(cur: Any, npo_id: str, grant_id: int, chunk_type: str) -> Tuple[int, Optional[str]]:
        return Gate4SemanticEvaluator._get_vector_similarity(cur, npo_id, grant_id, chunk_type)

    @classmethod
    def evaluate(cls, cur: Any, npo: Dict[str, Any], grant: Dict[str, Any]) -> Dict[str, Any]:
        g4 = Gate4SemanticEvaluator.evaluate(cur, npo, grant)
        return {
            "score": g4.score,
            "criteria_scores": g4.details.get("criteria_scores", {}),
            "evidence_quotes": g4.details.get("evidence_quotes", [])
        }


# ---------------------------------------------------------------------------
# EligibilityChecker: 6-Gate オーケストレータ
# ---------------------------------------------------------------------------

class EligibilityChecker:
    """Main Orchestrator for 6-Gate Grant Eligibility Evaluation"""

    _embedder = None

    def __init__(self, db_url: str):
        self.db_url = db_url

    @classmethod
    def get_embedder(cls):
        """BGE-M3 モデルをシングルトンで遅延ロード"""
        if cls._embedder is None:
            from sentence_transformers import SentenceTransformer
            cls._embedder = SentenceTransformer("BAAI/bge-m3")
        return cls._embedder

    def run(self, org_id: str, grant_id: str) -> Dict[str, Any]:
        with psycopg.connect(self.db_url, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM public.npo_profiles WHERE id = %s;", (org_id,))
                npo = cur.fetchone()
                if not npo:
                    raise ValueError(f"NPO Profile with ID '{org_id}' not found.")

                # Optimize PK index usage for integer ID vs text source_grant_id
                if grant_id.isdigit():
                    cur.execute(
                        "SELECT * FROM public.grants WHERE id = %s OR source_grant_id = %s;",
                        (int(grant_id), grant_id)
                    )
                else:
                    cur.execute(
                        "SELECT * FROM public.grants WHERE source_grant_id = %s;",
                        (grant_id,)
                    )
                grant = cur.fetchone()
                if not grant:
                    raise ValueError(f"Grant with ID '{grant_id}' not found.")

                gates: List[GateResult] = []

                # Gate 1: 基本ルール (早期打切り)
                g1 = Gate1BasicRuleEvaluator.evaluate(npo, grant)
                gates.append(g1)

                # Gate 2: 拠点要件 (早期打切り)
                g2 = Gate2LocationEvaluator.evaluate(npo, grant)
                gates.append(g2)

                # Gate 3: 予算規模 (早期打切り)
                g3 = Gate3BudgetEvaluator.evaluate(npo, grant)
                gates.append(g3)

                # 早期打切り判定
                early_fail = not all(g.passed for g in [g1, g2, g3])
                if early_fail:
                    failed_codes = [g.gate_code for g in gates if not g.passed]
                    return self._build_report(npo, grant, gates, "INELIGIBLE", 0, failed_codes)

                # Gate 4: セマンティック適合度
                g4 = Gate4SemanticEvaluator.evaluate(cur, npo, grant)
                gates.append(g4)

                # Gate 5: 特定要件 RAG
                g5 = Gate5RequirementRAGEvaluator.evaluate(cur, npo, grant, self.get_embedder())
                gates.append(g5)

                if g5.status == "FAIL":
                    failed_codes = [g5.gate_code]
                    return self._build_report(npo, grant, gates, "INELIGIBLE", 0, failed_codes)

                # Gate 6: 書類準備率
                g6 = Gate6DocumentEvaluator.evaluate(npo, grant)
                gates.append(g6)

                # スコア計算 & overall_status 判定
                total_score = int(g6.score * 0.4 + g4.score * 0.6)
                has_warn = any(g.status == "WARN" for g in gates)

                if total_score >= 70 and not has_warn:
                    overall_status = "ELIGIBLE"
                elif total_score >= 50 or has_warn:
                    overall_status = "CONDITIONAL"
                else:
                    overall_status = "INELIGIBLE"

                failed_codes = [g.gate_code for g in gates if not g.passed or g.status == "FAIL"]
                return self._build_report(npo, grant, gates, overall_status, total_score, failed_codes)

    def _build_report(self, npo, grant, gates, overall_status, total_score, failed_gate_codes):
        """統一レポートを構築し、DB に保存する"""
        # 後方互換用の旧キーを生成
        stage1_compat = Stage1RuleEvaluator.evaluate(npo, grant)
        stage2_compat = Stage2DocumentMatcher.evaluate(npo, grant)

        # Gate4 と Gate5 の旧形式を gates から取得
        g4_dict = next((g.to_dict() for g in gates if g.gate_code == "GATE_4"), {})
        g5_raw = next((g for g in gates if g.gate_code == "GATE_5"), None)
        gate5_compat = {
            "status": g5_raw.status if g5_raw else "SKIP",
            "items": g5_raw.details.get("items", []) if g5_raw else [],
            "reason": g5_raw.reason if g5_raw else ""
        }

        stage3_compat = {
            "score": g4_dict.get("score", 80),
            "criteria_scores": g4_dict.get("details", {}).get("criteria_scores", {}),
            "evidence_quotes": g4_dict.get("details", {}).get("evidence_quotes", [])
        }

        report = {
            "grant_id": grant["id"],
            "grant_title": grant["title"],
            "npo_profile_id": str(npo["id"]),
            "npo_name": npo["name"],
            "match_score": total_score,
            "overall_status": overall_status,
            "status": overall_status,  # 後方互換
            "failed_gate_codes": failed_gate_codes,
            "gates": [g.to_dict() for g in gates],
            # 後方互換キー
            "stage1_results": stage1_compat,
            "stage2_results": stage2_compat,
            "stage3_results": stage3_compat,
            "gate5_results": gate5_compat,
            "evaluated_at": datetime.now().isoformat()
        }

        self._upsert_alert(
            str(npo["id"]), grant["id"], grant["title"],
            total_score, overall_status, failed_gate_codes, report
        )

        return report

    def _upsert_alert(self, org_id: str, grant_id: int, title: str, score: int,
                      overall_status: str, failed_gate_codes: List[str], report: Dict[str, Any]):
        missing_count = len(report.get("stage2_results", {}).get("missing", []))
        msg = f"要件適合スコア: {score}% | 未準備書類: {missing_count}件 | 判定: {overall_status}"
        report_json = json.dumps(report, ensure_ascii=False, default=str)
        try:
            with psycopg.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO public.alerts
                            (npo_profile_id, grant_id, alert_type, title, message,
                             match_score, is_read, report_json, overall_status, failed_gate_codes)
                        VALUES (%s, %s, %s, %s, %s, %s, false, %s::jsonb, %s, %s)
                        ON CONFLICT ON CONSTRAINT uq_alerts_npo_grant_type
                        DO UPDATE SET
                            title = EXCLUDED.title,
                            message = EXCLUDED.message,
                            match_score = EXCLUDED.match_score,
                            report_json = EXCLUDED.report_json,
                            overall_status = EXCLUDED.overall_status,
                            failed_gate_codes = EXCLUDED.failed_gate_codes,
                            is_read = false,
                            created_at = NOW();
                        """,
                        (org_id, grant_id, "ELIGIBILITY_MATCH", f"【{overall_status}】{title}",
                         msg, score, report_json, overall_status, failed_gate_codes)
                    )
                conn.commit()
        except Exception as e:
            logging.warning(f"Could not save alert to DB: {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="6-Gate Grant Eligibility Checker")
    parser.add_argument("--org-id", required=True, help="NPO Profile UUID")
    parser.add_argument("--grant-id", required=True, help="Grant DB ID or source_grant_id")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")

    args = parser.parse_args()

    if not DATABASE_URL:
        print("❌ Error: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    checker = EligibilityChecker(DATABASE_URL)
    try:
        result = checker.run(args.org_id, args.grant_id)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            overall = result["overall_status"]
            status_icon = {"ELIGIBLE": "✅", "CONDITIONAL": "⚠️", "INELIGIBLE": "❌"}.get(overall, "❓")

            print("\n==================================================")
            print(f" 助成金要件適合判定レポート (6-Gate チェック)")
            print(f" 助成金: {result['grant_title']}")
            print(f" 団体名: {result['npo_name']}")
            print("==================================================")
            print(f" {status_icon} 判定: {overall} | スコア: {result['match_score']}%")
            if result.get("failed_gate_codes"):
                print(f" 不合格ゲート: {', '.join(result['failed_gate_codes'])}")
            print()

            for gate in result.get("gates", []):
                icon = "✅" if gate["passed"] else ("⚠️" if gate["status"] == "WARN" else "❌")
                print(f"  [{gate['gate_code']}] {gate['gate_name']}: {icon} {gate['status']} (スコア: {gate['score']})")

                # Gate 固有の詳細表示
                if gate["gate_code"] in ("GATE_1",):
                    for k, v in gate.get("details", {}).items():
                        sub_icon = "✅" if v.get("pass") else "❌"
                        print(f"    {sub_icon} {k}: {v.get('reason', '')}")

                elif gate["gate_code"] == "GATE_4":
                    for k, v in gate.get("details", {}).get("criteria_scores", {}).items():
                        print(f"    - {k}: {v}点")

                elif gate["gate_code"] == "GATE_5" and gate["status"] != "SKIP":
                    for item in gate.get("details", {}).get("items", []):
                        sub_icon = "✅" if item["status"] == "PASS" else ("⚠️" if item["status"] == "WARN" else "❌")
                        print(f"    {sub_icon} {item['grant_requirement'][:50]} → {item['similarity_score']:.2f}")

                elif gate["gate_code"] == "GATE_6":
                    missing = gate.get("details", {}).get("missing", [])
                    if missing:
                        print(f"    未準備: {', '.join(missing)}")

            print("==================================================\n")
    except Exception as e:
        print(f"❌ Evaluation Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
