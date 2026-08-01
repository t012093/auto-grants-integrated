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
        assert "必須セクション欠損" in str(excinfo.value)

    def test_harness_guard_raises_on_unreplaced_placeholder_key(self):
        """未置換のプレースホルダータグ {{key}} が残存している場合、抜け漏れエラーでストップする"""
        generator = ProposalGenerator(db_url=None)
        data = generator.fetch_data("org-123", "g-456", strict=False)
        md_content, meta = generator.generate_draft_sections(data)

        # 未置換のプレースホルダータグを注入
        unreplaced_md = md_content + "\n\n## 7. その他\n{{事業計画詳細}}"

        with pytest.raises(HarnessValidationError) as excinfo:
            generator.verify_harness(unreplaced_md, meta)
        assert "未置換タグ残存" in str(excinfo.value)
        assert "事業計画詳細" in str(excinfo.value)


class TestStrictMode:
    """--strict モードのテスト"""

    def test_strict_mode_raises_on_fallback(self):
        """strict=True でデータが不足している場合、例外で早期中断する"""
        generator = ProposalGenerator(db_url=None)
        with pytest.raises(ValueError) as excinfo:
            generator.fetch_data("00000000-0000-0000-0000-000000000000", "g-missing", strict=True)
        assert "Strict mode enabled" in str(excinfo.value)


class TestTemplateAnalysis:
    """officecli query による様式事前分析テスト"""

    def test_analyze_template_returns_type_a_for_marker_template(self, tmp_path, monkeypatch):
        """マーカー ({{key}}) を含む段落がある場合、タイプ A を返す"""
        import json
        generator = ProposalGenerator(db_url=None)

        # officecli query //p がマーカー付き段落を返すようモック
        def mock_query(file_path, selector):
            if selector == "//p":
                return [
                    {"path": "/body/p[1]", "text": "{{事業背景}}"},
                    {"path": "/body/p[2]", "text": "{{事業目的}}"},
                ]
            return []

        monkeypatch.setattr(generator, "_officecli_query", mock_query)
        profile = generator.analyze_template("dummy_template.docx")

        assert profile["type"] == "A"
        assert "事業背景" in profile["marker_paths"]
        assert "事業目的" in profile["marker_paths"]

    def test_analyze_template_returns_type_b_for_sdt_template(self, monkeypatch):
        """フォーム枠 (sdt) が存在しマーカーがない場合、タイプ B を返す"""
        generator = ProposalGenerator(db_url=None)

        def mock_query(file_path, selector):
            if selector == "sdt":
                return [
                    {"path": "/sdt[1]", "tag": "事業背景", "text": ""},
                    {"path": "/sdt[2]", "tag": "経費合計", "text": ""},
                ]
            return []

        monkeypatch.setattr(generator, "_officecli_query", mock_query)
        profile = generator.analyze_template("dummy_template.docx")

        assert profile["type"] == "B"
        assert "事業背景" in profile["sdt_paths"]
        assert "経費合計" in profile["sdt_paths"]

    def test_analyze_template_returns_type_c_for_table_only(self, monkeypatch):
        """表のみでマーカーもフォーム枠もない場合、タイプ C を返す"""
        generator = ProposalGenerator(db_url=None)

        def mock_query(file_path, selector):
            if selector == "table":
                return [{"path": "/body/tbl[1]"}]
            return []

        monkeypatch.setattr(generator, "_officecli_query", mock_query)
        profile = generator.analyze_template("dummy_template.docx")

        assert profile["type"] == "C"
        assert len(profile["table_paths"]) == 1


class TestOfficecliExportNoMock:
    """officecli 失敗時に Mock ファイルを生成せず例外をスローするかの検証"""

    def test_markdown_to_word_raises_on_officecli_failure(self, monkeypatch):
        """officecli create/add 失敗時に HarnessValidationError が発生する (Mock 生成されない)"""
        import subprocess
        generator = ProposalGenerator(db_url=None)

        def mock_run(*args, **kwargs):
            raise subprocess.CalledProcessError(1, "officecli", stderr="command not found")

        monkeypatch.setattr(subprocess, "run", mock_run)

        with pytest.raises(HarnessValidationError) as excinfo:
            generator._export_markdown_to_word(Path("/tmp/test.md"), Path("/tmp/test.docx"))
        assert "officecli create/add markdown failed" in str(excinfo.value)

    def test_excel_new_raises_on_officecli_failure(self, tmp_path, monkeypatch):
        """officecli Excel 新規作成の失敗時に HarnessValidationError が発生する (Mock 生成されない)"""
        import subprocess
        generator = ProposalGenerator(db_url=None)
        meta = {
            "allocated_items": [
                {"priority": 1, "category_label": "人件費", "desired_amount": 500000, "allocated_amount": 500000, "status": "APPROVED", "notes": ""}
            ]
        }

        def mock_run(*args, **kwargs):
            raise subprocess.CalledProcessError(1, "officecli", stderr="command not found")

        monkeypatch.setattr(subprocess, "run", mock_run)

        with pytest.raises(HarnessValidationError) as excinfo:
            generator._export_excel_new(tmp_path / "test.xlsx", meta, tmp_path, "g-test")
        assert "officecli Excel create/import failed" in str(excinfo.value)
