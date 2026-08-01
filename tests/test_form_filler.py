"""
Unit tests for ProposalGenerator (grant_form_filler).
"""

import pytest
import sys
from pathlib import Path

# skills/ 配下の generate_proposal_docx.py を直接 import するためパスを追加
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent
        / "skills"
        / "grant_form_filler"
        / "scripts"
    ),
)

from generate_proposal_docx import ProposalGenerator, HarnessValidationError


class TestProposalDraftGeneration:
    """申請原稿起草および 6 大セクションのテスト"""

    def test_generate_draft_sections_has_six_sections(self):
        generator = ProposalGenerator(db_url=None)
        data = generator.fetch_data("org-123", "g-456", strict=False)
        md_content, meta = generator.generate_draft_sections(data)

        # 6大セクションの存在確認
        assert "## 1. 事業の背景・社会的課題" in md_content
        assert "## 2. 事業目的" in md_content
        assert "## 3. 実施計画・月別スケジュール" in md_content
        assert "## 4. 実施体制・役割分担" in md_content
        assert "## 5. 期待される成果 (KPI)" in md_content
        assert "## 6. 経費明細" in md_content

        # 公募要領引用形式の確認
        assert "> **【公募要領 引用】**" in md_content

    def test_schedule_confirmation_note_when_no_period(self):
        """事業期間が要綱に未記載の場合、仮起草要確認注記が付与される"""
        generator = ProposalGenerator(db_url=None)
        data = generator.fetch_data("org-123", "g-456", strict=False)
        # detail_text に期間表記を含めない
        data["grant"]["detail_text"] = "地域活動の推進を目的とします。"

        md_content, _ = generator.generate_draft_sections(data)
        assert "💡 **[要確認: 公募要領に事業期間の明確な記載がないため" in md_content


class TestHarnessGuardVerification:
    """Harness Guard による算術・構造検証テスト"""

    def test_harness_guard_passes_on_valid_data(self):
        generator = ProposalGenerator(db_url=None)
        data = generator.fetch_data("org-123", "g-456", strict=False)
        md_content, meta = generator.generate_draft_sections(data)

        # 検証合格
        assert generator.verify_harness(md_content, meta) is True

    def test_harness_guard_raises_on_overflow(self):
        """配分額が助成上限を超過した場合、HarnessValidationError を発生させてストップする"""
        generator = ProposalGenerator(db_url=None)
        data = generator.fetch_data("org-123", "g-456", strict=False)
        md_content, meta = generator.generate_draft_sections(data)

        # オーバーフローをモック
        meta["total_allocated"] = meta["amount_max"] + 100000

        with pytest.raises(HarnessValidationError) as excinfo:
            generator.verify_harness(md_content, meta)
        assert "配分合計額" in str(excinfo.value)
        assert "を超過しています" in str(excinfo.value)

    def test_harness_guard_raises_on_missing_section(self):
        """必須セクションが欠損している場合、構造エラーでストップする"""
        generator = ProposalGenerator(db_url=None)
        meta = {"amount_max": 1000000, "total_allocated": 1000000}
        incomplete_md = "## 1. 事業の背景・社会的課題\n内容のみ"

        with pytest.raises(HarnessValidationError) as excinfo:
            generator.verify_harness(incomplete_md, meta)
        assert "必須セクションが欠損しています" in str(excinfo.value)

    def test_harness_guard_raises_on_unreplaced_placeholder_key(self):
        """未置換のプレースホルダータグ {{key}} が残存している場合、抜け漏れエラーでストップする"""
        generator = ProposalGenerator(db_url=None)
        data = generator.fetch_data("org-123", "g-456", strict=False)
        md_content, meta = generator.generate_draft_sections(data)

        # 未置換のプレースホルダータグを注入
        unreplaced_md = md_content + "\n\n## 7. その他\n{{事業計画詳細}}"

        with pytest.raises(HarnessValidationError) as excinfo:
            generator.verify_harness(unreplaced_md, meta)
        assert "未置換のプレースホルダータグが残存しています" in str(excinfo.value)
        assert "事業計画詳細" in str(excinfo.value)


class TestStrictMode:
    """--strict モードのテスト"""

    def test_strict_mode_raises_on_fallback(self):
        """strict=True でデータが不足している場合、例外で早期中断する"""
        generator = ProposalGenerator(db_url=None)
        with pytest.raises(ValueError) as excinfo:
            generator.fetch_data("00000000-0000-0000-0000-000000000000", "g-missing", strict=True)
        assert "Strict mode enabled" in str(excinfo.value)
