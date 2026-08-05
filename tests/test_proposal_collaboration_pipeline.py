#!/usr/bin/env python3
"""
Grant Proposal Collaboration Pipeline Tests

Phase 1〜3 の統合テスト:
  - DBスキーマのテーブル・カラム定義確認
  - タスク分離スキルの属性ラベル判定ロジック
  - ai-note-meet 連携スクリプトのペラ1生成 & 同期プラン出力
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from typing import Dict, List, Any

# テスト対象モジュール (sync_proposal_to_ai_note_meet) をインポート
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from sync_proposal_to_ai_note_meet import (
    generate_summary_page,
    AiNoteMeetSyncer,
)


# =============================================================================
# フィクスチャ
# =============================================================================

@pytest.fixture
def sample_proposal() -> Dict[str, Any]:
    """農水省500万円事業の企画書サンプル"""
    return {
        "id": "018f67bc-1234-7000-8000-000000000001",
        "npo_id": "00000000-0000-0000-0000-000000000001",
        "title": "【農水省500万】八尾・大長谷 AI/IT教育×子ども食堂モデル",
        "concept_summary": "大長谷の特産品をNPOが買い取り、子ども食堂×AI教室で活用しつつ全国EC販売し農家所得を還元",
        "status": "PARTNER_MATCHING",
        "content_markdown": "# 詳細企画書\n\n## 1. 地域の課題\n...",
        "budget_json": {
            "人件費・作業手当": 1800000,
            "食材買い取り代": 1000000,
            "EC開発・PRデザイン": 1200000,
            "会場費・備品購入": 700000,
            "旅費・交通費": 300000,
        },
        "kpi_json": {"参加人数": 200, "EC売上": 500000},
        "ai_note_project_id": None,
        "ai_note_page_id": None,
    }


@pytest.fixture
def sample_grants() -> List[Dict[str, Any]]:
    """紐づけ助成金サンプル"""
    return [
        {
            "grant_id": 42,
            "is_primary": True,
            "match_score": 83,
            "status": "CONSIDERING",
            "grant_title": "中山間地域所得確保推進事業",
            "subsidy_max_limit": 5000000,
            "grant_deadline": "2026-08-31",
        },
    ]


@pytest.fixture
def sample_offers() -> List[Dict[str, Any]]:
    """ポジションオファーサンプル"""
    return [
        {
            "id": 1,
            "position_code": "PM",
            "position_name": "プロジェクトリーダー",
            "capacity": 1,
            "task_allocation_tag": "HYBRID",
            "compensation_notes": "時給2,000円 × 200h = 40万円",
            "initial_tasks_json": [
                "富山市役所 農政企画課との事前相談",
                "500万円予算管理",
            ],
            "status": "RECRUITING",
        },
        {
            "id": 2,
            "position_code": "LOCAL_DIR",
            "position_name": "地域・農家連携ディレクター",
            "capacity": 1,
            "task_allocation_tag": "HUMAN",
            "compensation_notes": "時給1,500円 × 100h = 15万円",
            "initial_tasks_json": [
                "八尾・大長谷農家2名との面談・同意書獲得",
            ],
            "status": "RECRUITING",
        },
    ]


# =============================================================================
# テスト: ペラ1サマリーページ生成
# =============================================================================

class TestSummaryPageGeneration:
    """ペラ1サマリー Markdown 生成テスト"""

    def test_generates_valid_markdown(self, sample_proposal, sample_grants, sample_offers):
        """ペラ1が正しいMarkdownとして生成されること"""
        md = generate_summary_page(sample_proposal, sample_grants, sample_offers)
        assert isinstance(md, str)
        assert len(md) > 100

    def test_contains_project_title(self, sample_proposal, sample_grants, sample_offers):
        """プロジェクトタイトルが含まれること"""
        md = generate_summary_page(sample_proposal, sample_grants, sample_offers)
        assert sample_proposal["title"] in md

    def test_contains_grant_title(self, sample_proposal, sample_grants, sample_offers):
        """助成金名が含まれること"""
        md = generate_summary_page(sample_proposal, sample_grants, sample_offers)
        assert "中山間地域所得確保推進事業" in md

    def test_contains_match_score(self, sample_proposal, sample_grants, sample_offers):
        """AI適合スコアが含まれること"""
        md = generate_summary_page(sample_proposal, sample_grants, sample_offers)
        assert "83%" in md

    def test_contains_amount(self, sample_proposal, sample_grants, sample_offers):
        """申請金額が含まれること"""
        md = generate_summary_page(sample_proposal, sample_grants, sample_offers)
        assert "5,000,000円" in md

    def test_contains_budget_categories(self, sample_proposal, sample_grants, sample_offers):
        """予算カテゴリが含まれること"""
        md = generate_summary_page(sample_proposal, sample_grants, sample_offers)
        assert "人件費" in md
        assert "食材買い取り" in md

    def test_contains_positions(self, sample_proposal, sample_grants, sample_offers):
        """募集中ポジションが含まれること"""
        md = generate_summary_page(sample_proposal, sample_grants, sample_offers)
        assert "プロジェクトリーダー" in md
        assert "農家連携ディレクター" in md


# =============================================================================
# テスト: ai-note-meet 同期プラン
# =============================================================================

class TestAiNoteMeetSyncer:
    """ai-note-meet MCP 連携テスト"""

    def test_generates_plan_steps(self, sample_proposal, sample_grants, sample_offers):
        """正しい計画ステップ数が生成されること"""
        syncer = AiNoteMeetSyncer()
        log = syncer.sync(sample_proposal, sample_grants, sample_offers)
        # 期待ステップ: project(1) + summary_page(1) + detail_page(1) + calendar(1)
        #              + announcement(1) + tasks(3) + write_back(1) = 9
        assert len(log) == 9
        # 計画の最後は実行者(Agent)による DB 書き戻しステップ
        assert log[-1]["tool"] == "update_proposal_ai_note_ids"
        assert log[-1]["kind"] == "write_back"
        # write_back は create_project / create_page の返り値を $ref 参照で受け継ぐ
        assert log[-1]["args"]["ai_note_project_id"] == {"$ref": "#/steps/1/result.project_id"}
        assert log[-1]["args"]["ai_note_page_id"] == {"$ref": "#/steps/2/result.page_id"}
        assert log[-1]["deps"] == [1, 2]

    def test_idempotent_skip_when_already_synced(self, sample_proposal, sample_grants, sample_offers):
        """ai_note_project_id が設定済みなら、再同期せずスキップ計画のみ返す"""
        syncer = AiNoteMeetSyncer()
        already = dict(sample_proposal, ai_note_project_id="proj_123", ai_note_page_id="page_456")
        log = syncer.sync(already, sample_grants, sample_offers)
        assert len(log) == 1
        assert log[0]["tool"] == "__already_synced"
        assert log[0]["kind"] == "info"

    def test_first_step_is_create_project(self, sample_proposal, sample_grants, sample_offers):
        """最初のステップが create_project であること"""
        syncer = AiNoteMeetSyncer()
        log = syncer.sync(sample_proposal, sample_grants, sample_offers)
        assert log[0]["tool"] == "create_project"

    def test_summary_page_is_created(self, sample_proposal, sample_grants, sample_offers):
        """ペラ1サマリーページが生成されること"""
        syncer = AiNoteMeetSyncer()
        log = syncer.sync(sample_proposal, sample_grants, sample_offers)
        page_calls = [e for e in log if e["tool"] == "create_page"]
        assert len(page_calls) == 2  # ペラ1 + 詳細企画書
        assert "ペラ1" in page_calls[0]["args"]["title"]
        # ペラ1 は create_project(Step1) の project_id を受け継ぐ(孤立ページ防止)
        assert page_calls[0]["args"]["project_id"] == {"$ref": "#/steps/1/result.project_id"}
        # 詳細企画書はペラ1 の子ページ(parent_id 参照)として階層化される
        assert page_calls[1]["args"]["project_id"] == {"$ref": "#/steps/1/result.project_id"}
        assert page_calls[1]["args"]["parent_id"] == {"$ref": "#/steps/2/result.page_id"}

    def test_calendar_entry_created(self, sample_proposal, sample_grants, sample_offers):
        """カレンダーエントリが生成されること"""
        syncer = AiNoteMeetSyncer()
        log = syncer.sync(sample_proposal, sample_grants, sample_offers)
        cal_calls = [e for e in log if e["tool"] == "create_calendar_entry"]
        assert len(cal_calls) == 1
        assert "中山間地域" in cal_calls[0]["args"]["title"]
        # MCP 実契約: entry_category 必須 / deadline(助成金締切日) を date で伝播
        assert cal_calls[0]["args"]["entry_category"] == "助成金締切"
        assert cal_calls[0]["args"]["date"] == "2026-08-31"
        assert cal_calls[0]["args"]["project_id"] == {"$ref": "#/steps/1/result.project_id"}

    def test_announcement_created(self, sample_proposal, sample_grants, sample_offers):
        """オファーアナウンスが生成されること"""
        syncer = AiNoteMeetSyncer()
        log = syncer.sync(sample_proposal, sample_grants, sample_offers)
        ann_calls = [e for e in log if e["tool"] == "create_announcement"]
        assert len(ann_calls) == 1
        assert "メンバー募集" in ann_calls[0]["args"]["title"]
        # MCP 実契約: create_announcement は description を要求(旧 content キーは不使用)
        assert "description" in ann_calls[0]["args"]
        assert "content" not in ann_calls[0]["args"]

    def test_tasks_created_per_position(self, sample_proposal, sample_grants, sample_offers):
        """各ポジションのタスクが生成されること"""
        syncer = AiNoteMeetSyncer()
        log = syncer.sync(sample_proposal, sample_grants, sample_offers)
        task_calls = [e for e in log if e["tool"] == "create_task"]
        # PM: 2タスク + LOCAL_DIR: 1タスク = 3タスク
        assert len(task_calls) == 3
        # MCP 実契約: create_task は project_id と title が必須
        for tc in task_calls:
            assert tc["args"]["project_id"] == {"$ref": "#/steps/1/result.project_id"}
            assert tc["deps"] == [1]

    def test_no_announcement_if_no_recruiting(self, sample_proposal, sample_grants):
        """募集ポジションがない場合はアナウンスが生成されないこと"""
        filled_offers = [
            {
                "id": 1,
                "position_code": "PM",
                "position_name": "PM",
                "capacity": 1,
                "task_allocation_tag": "HYBRID",
                "compensation_notes": "",
                "initial_tasks_json": [],
                "status": "FILLED",
            }
        ]
        syncer = AiNoteMeetSyncer()
        log = syncer.sync(sample_proposal, sample_grants, filled_offers)
        ann_calls = [e for e in log if e["tool"] == "create_announcement"]
        assert len(ann_calls) == 0
