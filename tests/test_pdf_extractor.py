import pytest
from skills.jgrants_search.scripts.extract_pdf import PDFExtractor, STANDARD_DOC_MASTERS


def test_pdf_extractor_deterministic_docs():
    extractor = PDFExtractor()
    sample_text = (
        "公募要領\n"
        "【提出書類】\n"
        "1. 団体の定款または会則\n"
        "2. 前年度の決算書\n"
        "3. 役員名簿\n"
        "4. 履歴事項全部証明書（登記簿）\n"
    )
    docs = extractor.extract_required_documents_deterministic(sample_text)
    assert "ARTICLES" in docs
    assert "FINANCIAL_REPORT" in docs
    assert "BOARD_LIST" in docs
    assert "REGISTRY_CERTIFICATE" in docs

def test_pdf_extractor_deterministic_expenses():
    extractor = PDFExtractor()
    sample_text = (
        "対象経費について\n"
        "・システム開発費、広報費は対象です。\n"
        "・人件費は総事業費の 50% 以内とします。\n"
        "・懇親会費、飲食費は対象外とします。\n"
    )
    rules = extractor.extract_expense_rules_deterministic(sample_text)
    assert len(rules) >= 2
    personnel_rule = next((r for r in rules if r["category_code"] == "PERSONNEL"), None)
    assert personnel_rule is not None
    assert personnel_rule["max_ratio"] == 0.5


# --- クリティカル修正の検証テスト ---

def test_ocr_guard_clause_blocks_db_write():
    """クリティカル#2: 画像化PDF (テキスト<100文字) は status='ocr_required' で即中断"""
    extractor = PDFExtractor()
    # 空に近いテキストしか含まない PDF をシミュレート
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    # テキストなし (画像のみの PDF を模擬)
    pdf_bytes = doc.tobytes()
    doc.close()

    result = extractor.process_pdf(grant_id=999, pdf_bytes=pdf_bytes, pdf_name="scanned.pdf")
    assert result["status"] == "ocr_required"
    assert result["is_ocr_needed"] is True
    assert result["requirements"] is None
    assert "message" in result


def test_fallback_values_are_none_not_hardcoded():
    """クリティカル#3: 審査基準・目的・期間が抽出不可時は None (架空テキスト禁止)"""
    extractor = PDFExtractor()
    # 審査基準・目的・期間の記載がないテキスト
    text = "この文書にはキーワードが含まれていません。"
    reqs = extractor.extract_structured_requirements(text)

    assert reqs["evaluation_criteria"] is None
    assert reqs["funder_intent"] is None
    assert reqs["project_period"] is None


def test_extraction_coverage_metrics():
    """extraction_coverage が正しく計算される"""
    extractor = PDFExtractor()

    # 全項目抽出可能なテキスト
    full_text = (
        "審査基準: 地域課題の解決を評価します。\n"
        "目的: 市民活動の推進。\n"
        "令和8年4月1日〜令和9年3月31日\n"
        "提出書類: 定款、決算書\n"
        "対象経費: 人件費\n"
    )
    reqs = extractor.extract_structured_requirements(full_text)
    coverage = reqs["extraction_coverage"]

    assert coverage["evaluation_criteria"] is True
    assert coverage["funder_intent"] is True
    assert coverage["project_period"] is True
    assert coverage["required_documents"] is True
    assert coverage["expense_rules"] is True

    # 何も抽出できないテキスト
    empty_reqs = extractor.extract_structured_requirements("空テキスト")
    empty_cov = empty_reqs["extraction_coverage"]

    assert empty_cov["evaluation_criteria"] is False
    assert empty_cov["project_period"] is False


def test_expense_disallow_regex_sentence_boundary():
    """改善#5: 経費「対象外」判定が句点で区切られ、別文の対象外に引きずられない"""
    extractor = PDFExtractor()
    # 「人件費は対象です。懇親会費は対象外」→ 人件費は allowed=True のはず
    text = "人件費は対象です。懇親会費は対象外とします。"
    rules = extractor.extract_expense_rules_deterministic(text)

    personnel = next((r for r in rules if r["category_code"] == "PERSONNEL"), None)
    assert personnel is not None
    assert personnel["allowed"] is True, "人件費が別文の『対象外』に巻き込まれている"


def test_classify_chunk_type():
    """_classify_chunk_type が正しく分類する"""
    assert PDFExtractor._classify_chunk_type("審査基準について") == "EVALUATION_CRITERIA"
    assert PDFExtractor._classify_chunk_type("評価のポイント") == "EVALUATION_CRITERIA"
    assert PDFExtractor._classify_chunk_type("対象経費の一覧") == "EXPENSE_RULE"
    assert PDFExtractor._classify_chunk_type("対象外の経費") == "EXPENSE_RULE"
    assert PDFExtractor._classify_chunk_type("応募の流れ") == "GENERAL_REQUIREMENT"
