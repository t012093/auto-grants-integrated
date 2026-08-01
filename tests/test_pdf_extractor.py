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
