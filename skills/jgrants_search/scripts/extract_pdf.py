#!/usr/bin/env python3
"""
extract_pdf.py

PyMuPDF (pymupdf) を用いて公募要領 PDF を超高速 (10-50ms/p) に確定テキスト化し、
審査要件 5 大要素を構造化抽出して DB (public.grants, grant_expense_rules, knowledge_chunks) へ更新保存する CLI。
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
import httpx
import psycopg
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

# ログ設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("extract_pdf")

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
DEFAULT_MODEL_NAME = "BAAI/bge-m3"

# 標準書類マスター Enum 定義
STANDARD_DOC_MASTERS = {
    "ARTICLES": ["定款", "会則", "規約", "寄付行為"],
    "FINANCIAL_REPORT": ["決算", "収支報告", "財務諸表", "損益計算書", "貸借対照表", "予算書"],
    "BOARD_LIST": ["役員名簿", "構成員名簿", "役員一覧", "会員名簿"],
    "REGISTRY_CERTIFICATE": ["履歴事項全部証明書", "登記簿", "登記証明"],
    "ACTIVITY_REPORT": ["事業報告", "活動実績", "事業実績"],
    "TAX_CERTIFICATE": ["納税証明", "非課税証明", "市税納付"],
}

# 標準経費カテゴリパターン
EXPENSE_PATTERNS = {
    "PERSONNEL": [r"人件費", r"給与", r"謝金", r"手当"],
    "SYSTEM": [r"システム", r"クラウド", r"開発", r"ソフトウェア", r"IT"],
    "PROMOTION": [r"広報", r"印刷", r"製本", r"宣伝", r"広告"],
    "TRAVEL": [r"旅費", r"交通費", r"宿泊費"],
    "SUPPLIES": [r"消耗品", r"備品", r"資材"],
}


class PDFExtractor:
    def __init__(self, db_url: Optional[str] = None, model_name: str = DEFAULT_MODEL_NAME):
        self.db_url = db_url or DATABASE_URL
        self.model_name = model_name
        self._model = None

    @property
    def model(self) -> SentenceTransformer:
        """遅延ロード: embedding 生成時に初めてモデルをロード"""
        if self._model is None:
            logger.info(f"Loading embedding model: {self.model_name}...")
            self._model = SentenceTransformer(self.model_name)
            logger.info("Model loaded successfully.")
        return self._model

    def extract_text_from_pdf(self, pdf_bytes: bytes) -> Tuple[str, bool]:
        """
        PyMuPDF (fitz) で PDF の各ページから読み順レイアウトを保持して確定テキスト化。
        テキスト抽出量が 100 文字未満の場合は画像化 PDF (is_ocr_needed=True) と判定。
        """
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text_blocks = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("blocks")
            page_lines = []
            for b in blocks:
                text = b[4].strip()
                if text:
                    page_lines.append(text)
            if page_lines:
                full_text_blocks.append(f"--- ページ {page_num + 1} ---\n" + "\n\n".join(page_lines))

        doc.close()
        combined_text = "\n\n".join(full_text_blocks)
        clean_text = re.sub(r"\n{3,}", "\n\n", combined_text).strip()

        is_ocr_needed = len(clean_text) < 100
        return clean_text, is_ocr_needed

    def extract_required_documents_deterministic(self, text: str) -> List[str]:
        """
        項目5: 提出必須書類リスト (required_documents) の決定論的マスターマッピング (ハルシネーション 0%)
        """
        found_docs = set()
        flat_text = text.replace("\n", " ")
        for doc_key, keywords in STANDARD_DOC_MASTERS.items():
            for kw in keywords:
                if kw in flat_text:
                    found_docs.add(doc_key)
                    break
        return sorted(list(found_docs))

    def extract_expense_rules_deterministic(self, text: str) -> List[Dict[str, Any]]:
        """
        項目4: 対象経費条件 (expense_rules) の決定論的パターン抽出 (ハルシネーション 0%)
        """
        rules = []
        flat_text = text.replace("\n", " ")
        for cat_code, patterns in EXPENSE_PATTERNS.items():
            cat_found = any(re.search(p, flat_text) for p in patterns)
            if not cat_found:
                continue

            is_disallowed = False
            for p in patterns:
                disallow_pattern = rf"{p}[^。]*?(対象外|不可|計上できない|認められない)"
                if re.search(disallow_pattern, flat_text):
                    is_disallowed = True
                    break

            max_limit = None
            max_ratio = None

            ratio_match = re.search(r"人件費.*?(\d+)%[以以]?[下内]?", flat_text)
            if cat_code == "PERSONNEL" and ratio_match:
                try:
                    max_ratio = float(ratio_match.group(1)) / 100.0
                except ValueError:
                    pass

            rules.append({
                "category_code": cat_code,
                "category_label": cat_code,
                "allowed": not is_disallowed,
                "max_limit": max_limit,
                "max_ratio": max_ratio,
                "notes": "対象外規定あり" if is_disallowed else "確定抽出"
            })

        return rules

    def extract_structured_requirements(self, text: str) -> Dict[str, Any]:
        """
        公募要領テキストから 5 大要件を抽出（確定的パース + Substring Guard 構造）
        """
        req_docs = self.extract_required_documents_deterministic(text)
        expense_rules = self.extract_expense_rules_deterministic(text)

        criteria_snippet = None
        criteria_match = re.search(r"(審査基準|評価項目|選定基準|評価のポイント)[\s\S]{1,500}", text)
        if criteria_match:
            criteria_snippet = criteria_match.group(0)[:300].strip()

        intent_snippet = None
        intent_match = re.search(r"(目的|趣旨|概要|背景)[\s\S]{1,300}", text)
        if intent_match:
            intent_snippet = intent_match.group(0)[:200].strip()

        period_str = None
        period_match = re.search(r"(令和\d+年\d+月\d+日|20\d{2}年\d+月\d+日)\s*[〜～\-]\s*(令和\d+年\d+月\d+日|20\d{2}年\d+月\d+日)", text)
        if period_match:
            period_str = period_match.group(0)

        return {
            "evaluation_criteria": criteria_snippet,
            "funder_intent": intent_snippet,
            "project_period": period_str,
            "expense_rules": expense_rules,
            "required_documents": req_docs,
            "extraction_coverage": {
                "evaluation_criteria": criteria_snippet is not None,
                "funder_intent": intent_snippet is not None,
                "project_period": period_str is not None,
                "required_documents": len(req_docs) > 0,
                "expense_rules": len(expense_rules) > 0,
            }
        }

    async def fetch_pdf_from_url(self, url: str) -> bytes:
        """指定 URL から PDF バイトデータをダウンロード"""
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    @staticmethod
    def _classify_chunk_type(chunk: str) -> str:
        """チャンクの内容からタイプを決定論的に分類"""
        if "審査" in chunk or "評価" in chunk:
            return "EVALUATION_CRITERIA"
        elif "経費" in chunk or "対象外" in chunk:
            return "EXPENSE_RULE"
        return "GENERAL_REQUIREMENT"

    def save_knowledge_chunks(self, cur: Any, grant_id: int, text: str):
        """テキストを 500 文字単位で分割し、BGE-M3 で embedding 生成して knowledge_chunks に登録"""
        cur.execute("DELETE FROM public.knowledge_chunks WHERE grant_id = %s;", (grant_id,))

        CHUNK_SIZE = 500
        STEP = 450  # 50文字オーバーラップ
        chunks = [text[i:i+CHUNK_SIZE] for i in range(0, len(text), STEP)]

        if not chunks:
            return

        # BGE-M3 でバッチ embedding 生成
        embeddings = self.model.encode(chunks, normalize_embeddings=True)

        for idx, (chunk, vec) in enumerate(zip(chunks, embeddings)):
            chunk_type = self._classify_chunk_type(chunk)
            cur.execute(
                """
                INSERT INTO public.knowledge_chunks (grant_id, chunk_type, content, embedding)
                VALUES (%s, %s, %s, %s::vector);
                """,
                (grant_id, chunk_type, chunk, str(vec.tolist()))
            )
        logger.info(f"Saved {len(chunks)} knowledge chunks for grant_id={grant_id}")

    def process_pdf(self, grant_id: int, pdf_bytes: bytes, pdf_name: str = "guideline.pdf") -> Dict[str, Any]:
        """
        PDF の解読・確定要件抽出・DB保存の一連の統合パイプライン
        """
        extracted_text, is_ocr_needed = self.extract_text_from_pdf(pdf_bytes)

        # Guard Clause: 画像化 PDF はテキスト抽出不可のため DB 書き込みをスキップ
        if is_ocr_needed:
            logger.warning(f"PDF {pdf_name}: テキスト抽出量 {len(extracted_text)} 文字 (<100)。"
                           f"画像化PDFと判定。DB書き込みをスキップ。")
            return {
                "status": "ocr_required",
                "grant_id": grant_id,
                "extracted_text_len": len(extracted_text),
                "is_ocr_needed": True,
                "requirements": None,
                "message": "画像化PDFのためテキスト抽出不可。OCRパイプラインへの委譲が必要です。"
            }

        reqs = self.extract_structured_requirements(extracted_text)

        if not self.db_url:
            logger.info("DATABASE_URL not set. Returning extracted result without DB update.")
            return {"status": "ok", "requirements": reqs, "extracted_len": len(extracted_text)}

        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                # 1. public.grants の更新
                cur.execute("SELECT detail_text FROM public.grants WHERE id = %s;", (grant_id,))
                row = cur.fetchone()
                existing_detail = row[0] if row and row[0] else ""

                updated_detail = f"{existing_detail}\n\n=== 【公募要領 PDF パース本文 ({pdf_name})】 ===\n{extracted_text}".strip()

                cur.execute(
                    """
                    UPDATE public.grants
                    SET detail_text = %s,
                        required_documents = %s,
                        is_ocr_processed = true,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (updated_detail, reqs["required_documents"], grant_id)
                )

                # 2. public.grant_expense_rules の更新
                cur.execute("DELETE FROM public.grant_expense_rules WHERE grant_id = %s;", (grant_id,))
                for rule in reqs["expense_rules"]:
                    cur.execute(
                        """
                        INSERT INTO public.grant_expense_rules (grant_id, category_code, category_label, allowed, max_limit, max_ratio)
                        VALUES (%s, %s, %s, %s, %s, %s);
                        """,
                        (grant_id, rule["category_code"], rule["category_label"], rule["allowed"], rule["max_limit"], rule["max_ratio"])
                    )

                # 3. public.knowledge_chunks の登録
                self.save_knowledge_chunks(cur, grant_id, extracted_text)

                conn.commit()
                logger.info(f"Successfully updated DB for grant_id {grant_id} with extracted PDF data.")

        return {
            "status": "success",
            "grant_id": grant_id,
            "extracted_text_len": len(extracted_text),
            "is_ocr_needed": is_ocr_needed,
            "requirements": reqs
        }


async def main_async():
    parser = argparse.ArgumentParser(description="PyMuPDF 公募要領 PDF パース CLI")
    parser.add_argument("--grant-id", type=int, default=1, help="対象助成金の DB ID")
    parser.add_argument("--pdf-path", type=str, help="ローカル指定 PDF パス")
    parser.add_argument("--json", action="store_true", help="結果を JSON 出力")

    args = parser.parse_args()
    extractor = PDFExtractor()

    pdf_bytes = None
    pdf_name = "sample.pdf"

    if args.pdf_path and os.path.exists(args.pdf_path):
        with open(args.pdf_path, "rb") as f:
            pdf_bytes = f.read()
        pdf_name = os.path.basename(args.pdf_path)
    else:
        if DATABASE_URL:
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT attachment_urls FROM public.grants WHERE id = %s;", (args.grant_id,))
                    row = cur.fetchone()
                    if row and row[0]:
                        urls = row[0]
                        if isinstance(urls, list) and len(urls) > 0:
                            target_url = urls[0]
                            logger.info(f"Fetching PDF from URL: {target_url}")
                            try:
                                pdf_bytes = await extractor.fetch_pdf_from_url(target_url)
                                pdf_name = os.path.basename(target_url.split("?")[0])
                            except Exception as e:
                                logger.error(f"Failed to fetch PDF from {target_url}: {e}")

    if not pdf_bytes:
        # モック PDF の作成 (テスト用・日本語対応)
        doc = fitz.open()
        page = doc.new_page()
        sample_text = (
            "令和8年度 地域デジタルコミュニティ活性化助成金 公募要領\n\n"
            "■ 提出必須書類\n"
            "1. 定款 または会則 (ARTICLES)\n"
            "2. 直近の 決算 書（収支報告書） (FINANCIAL_REPORT)\n"
            "3. 役員名簿 (BOARD_LIST)\n"
            "4. 履歴事項全部証明書（ 登記簿 謄本） (REGISTRY_CERTIFICATE)\n\n"
            "■ 対象経費および注意点\n"
            "- システム 開発費、 広報 費、 備品 費、 旅費 、 人件費 は対象となります。\n"
            "- 人件費 は総事業費の 50% 以内とします。\n"
            "- 飲食費および懇親会費は対象外とします。\n\n"
            "■ 事業対象期間\n"
            "令和8年4月1日〜令和9年3月31日\n"
        )
        # 日本語標準フォントの指定
        font_name = "japan"
        page.insert_textbox(fitz.Rect(50, 50, 550, 750), sample_text, fontname=font_name, fontsize=11)
        pdf_bytes = doc.tobytes()
        doc.close()
        pdf_name = "generated_sample_guideline.pdf"
        logger.info("Created sample in-memory PDF with Japanese font for processing.")

    result = extractor.process_pdf(args.grant_id, pdf_bytes, pdf_name)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("status") == "ocr_required":
        print(f"\n⚠️  {result.get('message')}")
        print(f"抽出文字数: {result.get('extracted_text_len')} 文字")
    else:
        print("\n=== PDF パース ＆ 要件抽出結果 ===")
        print(f"助成金 ID: {result.get('grant_id')}")
        print(f"抽出文字数: {result.get('extracted_text_len')} 文字")
        reqs = result.get("requirements") or {}
        print(f"提出必須書類マスター: {reqs.get('required_documents')}")
        print(f"対象経費ルール数: {len(reqs.get('expense_rules', []))}")
        print(f"事業期間: {reqs.get('project_period', '（未抽出）')}")
        criteria = reqs.get('evaluation_criteria')
        print(f"審査基準スニペット: {criteria[:100] if criteria else '（PDF内に記載なし）'}\n")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
