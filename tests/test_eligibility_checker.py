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

# --- 都道府県前方一致テスト (area_match) ---
from skills.grant_eligibility_checker.scripts.check_eligibility import area_match, normalize_prefecture

def test_normalize_prefecture():
    assert normalize_prefecture("東京都千代田区") == "東京都"
    assert normalize_prefecture("京都府京都市") == "京都府"
    assert normalize_prefecture("北海道札幌市") == "北海道"
    assert normalize_prefecture("全国") == "全国"  # マッチなし→原文返却

def test_area_match_kyoto_vs_tokyo_no_false_match():
    """京都府の公募に東京都の団体がマッチしないことを検証"""
    assert area_match("京都府", "東京都千代田区") is False
    assert area_match("京都", "東京都千代田区") is False

def test_area_match_kyoto_correct_match():
    """京都府の公募に京都府の団体がマッチすることを検証"""
    assert area_match("京都府", "京都府京都市") is True

def test_area_match_zenkoku_always_pass():
    """団体の所在地が「全国」ならどの公募エリアでもマッチ"""
    assert area_match("東京都", "全国") is True

def test_stage1_location_kyoto_vs_tokyo_no_false_match():
    """Stage1統合テスト: 京都府公募に東京都の団体がFAILすること"""
    npo = {
        "organization_type": "NPO_CORPORATION",
        "establishment_year": 2020,
        "annual_budget": 10000000,
        "headquarter_location": "東京都千代田区",
        "branch_locations": [],
        "activity_areas": []
    }
    grant = {
        "target_area": "京都府",
        "location_requirement_type": "HEADQUARTER_ONLY",
        "eligible_org_types": ["NPO_CORPORATION"],
        "min_years_active": 0,
        "amount_max": 1000000,
        "status": "OPEN"
    }
    res = Stage1RuleEvaluator.evaluate(npo, grant)
    assert res["details"]["target_area"]["pass"] is False

def test_stage1_location_kyoto_match():
    """Stage1統合テスト: 京都府公募に京都府の団体がPASSすること"""
    npo = {
        "organization_type": "NPO_CORPORATION",
        "establishment_year": 2020,
        "annual_budget": 10000000,
        "headquarter_location": "京都府京都市",
        "branch_locations": [],
        "activity_areas": []
    }
    grant = {
        "target_area": "京都府",
        "location_requirement_type": "HEADQUARTER_ONLY",
        "eligible_org_types": ["NPO_CORPORATION"],
        "min_years_active": 0,
        "amount_max": 1000000,
        "status": "OPEN"
    }
    res = Stage1RuleEvaluator.evaluate(npo, grant)
    assert res["details"]["target_area"]["pass"] is True


# --- Gate 5: extract_requirement_sentences テスト ---

def test_extract_requirement_sentences_basic():
    """基本的な要件文抽出"""
    extractor = PDFExtractor(db_url=None)
    text = """
    応募資格
    以下の要件を全て満たす団体が対象となります。
    ・特定非営利活動法人として認定を受けていること
    ・設立から3年以上の活動実績を有する団体であること
    ・東京都内に主たる事務所が所在すること
    申請方法
    所定の申請書に必要事項を記入してください。
    """
    sentences = extractor.extract_requirement_sentences(text)
    assert len(sentences) >= 2
    assert any("認定" in s or "法人" in s for s in sentences)
    assert any("実績" in s or "活動" in s for s in sentences)

def test_extract_requirement_sentences_max_15():
    """最大15件制限の検証"""
    extractor = PDFExtractor(db_url=None)
    lines = "\n".join([f"・要件{i}: 特定非営利活動法人として認定を受けた団体であること" for i in range(20)])
    text = f"応募資格\n{lines}\n申請方法"
    sentences = extractor.extract_requirement_sentences(text)
    assert len(sentences) <= 15

def test_extract_requirement_sentences_short_filter():
    """10文字未満の短文が除外されること"""
    extractor = PDFExtractor(db_url=None)
    text = """
    応募資格
    ・対象
    ・NPO法人として認定を受けた実績のある団体であること
    申請方法
    """
    sentences = extractor.extract_requirement_sentences(text)
    assert all(len(s) >= 10 for s in sentences)


# --- Gate 5: Gate5RequirementRAGEvaluator テスト ---
from skills.grant_eligibility_checker.scripts.check_eligibility import Gate5RequirementRAGEvaluator

def test_gate5_skip_when_no_sentences():
    """requirement_sentences が空なら SKIP を返す"""
    grant = {"requirement_sentences": []}
    npo = {"id": "test-npo-id"}
    result = Gate5RequirementRAGEvaluator.evaluate(None, npo, grant, None)
    assert result["status"] == "SKIP"
    assert result["items"] == []

def test_gate5_explanation_template():
    """テンプレート解説の構造検証"""
    explanation, advice = Gate5RequirementRAGEvaluator._generate_explanation(
        "NPO法人であること", "活動分野: 子ども支援", 0.45, "FAIL"
    )
    assert "NPO法人であること" in explanation
    assert "0.45" in explanation
    assert "再判定" in advice


