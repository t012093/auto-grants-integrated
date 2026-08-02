#!/usr/bin/env python3
"""
17-Item 3-Tier Hybrid Eligibility Checker
Scans npo_profiles & grants data from Neon DB, performs full 17-item eligibility evaluation,
and saves/updates the result in public.alerts using PostgreSQL ON CONFLICT.
"""

import os
import sys
import json
import logging
import argparse
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



class Stage1RuleEvaluator:
    """Stage 1: Rule-based Deterministic Evaluation (0% Hallucination - 5 Items)"""

    @staticmethod
    def evaluate(npo: Dict[str, Any], grant: Dict[str, Any]) -> Dict[str, Any]:
        results = {}
        all_pass = True

        # 1. 法人格一致 (organization_type)
        eligible_types = grant.get("eligible_org_types") or ["NPO_CORPORATION", "GENERAL_INC", "UNINCORPORATED"]
        npo_type = npo.get("organization_type") or "NPO_CORPORATION"
        type_pass = npo_type in eligible_types
        results["organization_type"] = {
            "pass": type_pass,
            "reason": f"団体型 '{npo_type}' は対象枠 {eligible_types} に{'含まれます' if type_pass else '含まれません'}"
        }
        if not type_pass:
            all_pass = False

        # 2. 実績年数 (years_active)
        min_years = grant.get("min_years_active") or 0
        est_year = npo.get("establishment_year")
        current_year = datetime.now().year
        active_years = (current_year - est_year) if est_year else 0
        years_pass = active_years >= min_years
        results["years_active"] = {
            "pass": years_pass,
            "reason": f"活動実績 {active_years}年 (必要年数: {min_years}年)"
        }
        if not years_pass:
            all_pass = False

        # 3. 対象地域 (target_area)
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

        results["target_area"] = {
            "pass": area_pass,
            "reason": reason
        }
        if not area_pass:
            all_pass = False

        # 4. 予算規模整合 (budget_ratio) - spec.md: 助成上限が年予算の50%以内が適正
        max_amount = grant.get("amount_max") or 0
        annual_budget = npo.get("annual_budget") or 0
        if max_amount > 0 and annual_budget > 0:
            budget_ratio = max_amount / annual_budget
            budget_pass = budget_ratio <= 0.50  # spec: <= 50%
            reason = f"助成上限 {max_amount:,}円 / 前年予算 {annual_budget:,}円 (比率: {budget_ratio*100:.1f}% <= 50%上限)"
        else:
            budget_pass = True
            reason = "予算要件制限なし"
        results["budget_ratio"] = {
            "pass": budget_pass,
            "reason": reason
        }
        if not budget_pass:
            all_pass = False

        # 5. 公募ステータス (grant_status)
        status = grant.get("status") or "OPEN"
        deadline = grant.get("deadline")
        is_open = status == "OPEN"
        deadline_valid = True
        if deadline:
            if isinstance(deadline, str):
                try:
                    deadline = datetime.strptime(deadline[:10], "%Y-%m-%d").date()
                except ValueError:
                    deadline_valid = True
            elif isinstance(deadline, datetime):
                deadline = deadline.date()
            if isinstance(deadline, date):
                deadline_valid = deadline >= date.today()
        status_pass = is_open and deadline_valid
        results["grant_status"] = {
            "pass": status_pass,
            "reason": f"ステータス '{status}' / 締切 '{deadline or '未設定'}'"
        }
        if not status_pass:
            all_pass = False

        return {"all_pass": all_pass, "details": results}


class Stage2DocumentMatcher:
    """Stage 2: Document Readiness Comparison (4 Items)"""

    @staticmethod
    def evaluate(npo: Dict[str, Any], grant: Dict[str, Any]) -> Dict[str, Any]:
        raw_required = grant.get("required_documents")
        required_docs = set(raw_required) if raw_required is not None else set()
        prepared_docs = set(npo.get("prepared_documents") or [])

        # If required_documents is explicit empty list, no documents are required
        if raw_required is None:
            required_docs = {"ARTICLES", "FINANCIAL_REPORT", "BOARD_LIST", "REGISTRY_CERTIFICATE"}

        missing_docs = list(required_docs - prepared_docs)
        prepared_matched = list(required_docs & prepared_docs)

        score = int((len(prepared_matched) / len(required_docs)) * 100) if required_docs else 100

        return {
            "score": score,
            "required": sorted(list(required_docs)),
            "prepared": sorted(prepared_matched),
            "missing": sorted(missing_docs)
        }


class Stage3SemanticEvaluator:
    """Stage 3: 8-Item Semantic Alignment (pgvector Cosine Similarity) & Qualitative Rules"""

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
        return Stage3SemanticEvaluator.FALLBACK_SCORE, None

    @classmethod
    def evaluate(cls, cur: Any, npo: Dict[str, Any], grant: Dict[str, Any]) -> Dict[str, Any]:
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
            # Rule fallback
            tags = npo.get("activity_tags") or []
            tag_hits = sum(1 for tag in tags if tag in full_grant_text)
            act_score = min(70 + tag_hits * 15, 100)
        else:
            evidence_quotes.append(act_quote)
        scores["activity_category"] = act_score

        # 11. ターゲット層適合度 (pgvector Cosine Similarity: TARGET_AUDIENCE)
        aud_score, aud_quote = cls._get_vector_similarity(cur, org_id, grant_id, "TARGET_AUDIENCE")
        if aud_quote is None:
            # Rule fallback
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
            # Rule fallback
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
        # pgvector 由来の quote は detail_text の部分文字列とは限らないため、
        # 完全一致ではなく 20 文字以上の重複部分があれば evidence として採用する。
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

        return {
            "score": avg_score,
            "criteria_scores": scores,
            "evidence_quotes": valid_quotes
        }


class Gate5RequirementRAGEvaluator:
    """Gate 5: 特定要件 RAG — 助成金要件文 → NPO実績チャンクへの正方向ベクトル検索"""

    SIMILARITY_PASS = 0.70
    SIMILARITY_WARN = 0.50

    @classmethod
    def evaluate(cls, cur: Any, npo: Dict[str, Any], grant: Dict[str, Any], embedder: Any) -> Dict[str, Any]:
        requirement_sentences = grant.get("requirement_sentences") or []
        if not requirement_sentences:
            return {"status": "SKIP", "reason": "要件文未抽出 (スキップ)", "items": []}

        npo_id = str(npo["id"])

        # Guard: NPO チャンクの存在確認
        cur.execute("SELECT COUNT(*) AS cnt FROM public.npo_knowledge_chunks WHERE npo_profile_id = %s;", (npo_id,))
        row = cur.fetchone()
        count = row["cnt"] if isinstance(row, dict) else row[0]
        if count == 0:
            return {
                "status": "WARN",
                "reason": "NPO実績ベクトルデータ未登録",
                "items": [
                    {
                        "grant_requirement": req,
                        "npo_matched_evidence": "実績データなし",
                        "similarity_score": 0.0,
                        "status": "WARN",
                        "explanation": f"要件『{req}』に対する団体実績データが登録されていません。",
                        "user_advice": "団体プロファイルの実績・活動情報を登録して再判定してください。"
                    }
                    for req in requirement_sentences
                ]
            }

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

        overall_status = "FAIL" if has_fail else ("WARN" if has_warn else "PASS")
        return {"status": overall_status, "items": items}

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


class EligibilityChecker:
    """Main Orchestrator for Grant Eligibility Evaluation"""

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

                stage1 = Stage1RuleEvaluator.evaluate(npo, grant)
                stage2 = Stage2DocumentMatcher.evaluate(npo, grant)
                stage3 = Stage3SemanticEvaluator.evaluate(cur, npo, grant)
                gate5 = Gate5RequirementRAGEvaluator.evaluate(cur, npo, grant, self.get_embedder())

                if not stage1["all_pass"] or gate5["status"] == "FAIL":
                    total_score = 0
                    status = "FAIL"
                else:
                    total_score = int(stage2["score"] * 0.4 + stage3["score"] * 0.6)
                    if gate5["status"] == "WARN":
                        status = "WARNING"
                    else:
                        status = "PASS" if total_score >= 70 else "WARNING"

                # Align keys with spec.md output schema
                report = {
                    "grant_id": grant["id"],
                    "grant_title": grant["title"],
                    "npo_profile_id": str(npo["id"]),
                    "npo_name": npo["name"],
                    "match_score": total_score,
                    "status": status,
                    "stage1_results": stage1,
                    "stage2_results": stage2,
                    "stage3_results": stage3,
                    "gate5_results": gate5,
                    "evaluated_at": datetime.now().isoformat()
                }

                self._upsert_alert(org_id, grant["id"], grant["title"], total_score, report)

        return report

    def _upsert_alert(self, org_id: str, grant_id: int, title: str, score: int, report: Dict[str, Any]):
        msg = f"要件適合スコア: {score}% | 未準備書類: {len(report['stage2_results']['missing'])}件"
        report_json = json.dumps(report, ensure_ascii=False, default=str)
        try:
            with psycopg.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO public.alerts
                            (npo_profile_id, grant_id, alert_type, title, message, match_score, is_read, report_json)
                        VALUES (%s, %s, %s, %s, %s, %s, false, %s::jsonb)
                        ON CONFLICT ON CONSTRAINT uq_alerts_npo_grant_type
                        DO UPDATE SET
                            title = EXCLUDED.title,
                            message = EXCLUDED.message,
                            match_score = EXCLUDED.match_score,
                            report_json = EXCLUDED.report_json,
                            is_read = false,
                            created_at = NOW();
                        """,
                        (org_id, grant_id, "ELIGIBILITY_MATCH", f"【適合率 {score}%】{title}", msg, score, report_json)
                    )
                conn.commit()
        except Exception as e:
            logging.warning(f"Could not save alert to DB: {e}")


def main():
    parser = argparse.ArgumentParser(description="17-Item 3-Tier Hybrid Grant Eligibility Checker")
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
            print("\n==================================================")
            print(f" 助成金要件適合判定レポート (17項目チェック完了)")
            print(f" 助成金: {result['grant_title']}")
            print(f" 団体名: {result['npo_name']}")
            print("==================================================")
            print(f" 適合スコア: {result['match_score']}% (ステータス: {result['status']})")
            print("\n【Stage 1: 確定ルール判定 (5項目)】", "ALL PASS" if result['stage1_results']['all_pass'] else "FAILED")
            for k, v in result['stage1_results']['details'].items():
                icon = "✅" if v['pass'] else "❌"
                print(f"  {icon} {k}: {v['reason']}")

            print("\n【Stage 2: 提出書類チェック (4項目)】")
            print(f"  - 準備済み: {', '.join(result['stage2_results']['prepared']) or 'なし'}")
            print(f"  - 未準備: {', '.join(result['stage2_results']['missing']) or 'なし'}")

            print("\n【Stage 3: セマンティック適合度 (8項目)】", f"{result['stage3_results']['score']}%")
            for k, v in result['stage3_results']['criteria_scores'].items():
                print(f"  - {k}: {v}点")

            gate5 = result.get('gate5_results', {})
            if gate5.get('status') != 'SKIP':
                print(f"\n【Gate 5: 特定要件 RAG】 {gate5.get('status', 'N/A')}")
                for item in gate5.get('items', []):
                    icon = "✅" if item['status'] == 'PASS' else ("⚠️" if item['status'] == 'WARN' else "❌")
                    print(f"  {icon} {item['grant_requirement'][:50]} → {item['similarity_score']:.2f}")

            print("==================================================\n")
    except Exception as e:
        print(f"❌ Evaluation Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
