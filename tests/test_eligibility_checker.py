import pytest
from skills.grant_eligibility_checker.scripts.check_eligibility import Stage1RuleEvaluator
from skills.jgrants_search.scripts.extract_pdf import PDFExtractor

def test_stage1_location_headquarter_only_fail():
    npo = {
        "organization_type": "NPO_CORPORATION",
        "establishment_year": 2020,
        "annual_budget": 10000000,
        "headquarter_location": "富山県富山市",
        "branch_locations": ["東京都千代田区"],
        "activity_areas": ["東京都", "富山県"]
    }
    grant = {
        "target_area": "東京都",
        "location_requirement_type": "HEADQUARTER_ONLY",
        "eligible_org_types": ["NPO_CORPORATION"],
        "min_years_active": 0,
        "amount_max": 1000000,
        "status": "OPEN"
    }
    res = Stage1RuleEvaluator.evaluate(npo, grant)
    assert res["all_pass"] is False
    assert res["details"]["target_area"]["pass"] is False
    assert "本店限定要件" in res["details"]["target_area"]["reason"]

def test_stage1_location_branch_allowed_pass():
    npo = {
        "organization_type": "NPO_CORPORATION",
        "establishment_year": 2020,
        "annual_budget": 10000000,
        "headquarter_location": "富山県富山市",
        "branch_locations": ["東京都千代田区"],
        "activity_areas": ["東京都", "富山県"]
    }
    grant = {
        "target_area": "東京都",
        "location_requirement_type": "BRANCH_ALLOWED",
        "eligible_org_types": ["NPO_CORPORATION"],
        "min_years_active": 0,
        "amount_max": 1000000,
        "status": "OPEN"
    }
    res = Stage1RuleEvaluator.evaluate(npo, grant)
    assert res["all_pass"] is True
    assert res["details"]["target_area"]["pass"] is True
    assert "支店認容要件" in res["details"]["target_area"]["reason"]

def test_stage1_location_legacy_fallback():
    npo = {
        "organization_type": "NPO_CORPORATION",
        "establishment_year": 2020,
        "annual_budget": 10000000,
        "location": "東京都千代田区"
    }
    grant = {
        "target_area": "東京都",
        "location_requirement_type": "BRANCH_ALLOWED",
        "eligible_org_types": ["NPO_CORPORATION"],
        "min_years_active": 0,
        "amount_max": 1000000,
        "status": "OPEN"
    }
    res = Stage1RuleEvaluator.evaluate(npo, grant)
    assert res["all_pass"] is True
    assert res["details"]["target_area"]["pass"] is True

def test_pdf_extractor_location_requirement():
    extractor = PDFExtractor(db_url=None)
    hq_text = "本事業の対象者は、主たる事務所が東京都内に所在する法人に限る。"
    assert extractor.extract_location_requirement_deterministic(hq_text) == "HEADQUARTER_ONLY"

    branch_text = "公募対象: 東京都内に本店または支店・営業所を有する事業者。"
    assert extractor.extract_location_requirement_deterministic(branch_text) == "BRANCH_ALLOWED"
