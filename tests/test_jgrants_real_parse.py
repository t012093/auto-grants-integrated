"""
実データの jGrants JSON レスポンスに対するパース・正規化検証テスト (test_jgrants_real_parse.py)
"""

import json
from pathlib import Path
import pytest

def test_jgrants_real_snapshot_parse():
    snapshot_file = Path(".cache/snapshots/jgrants_real_sample.json")
    assert snapshot_file.exists(), "Snapshot file must exist"
    
    with open(snapshot_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    metadata = data.get("metadata", {})
    count = metadata.get("resultset", {}).get("count", 0)
    result_list = data.get("result", [])
    
    assert count > 0
    assert len(result_list) > 0
    
    # 最初のアイテムのフィールド構造をテスト
    first = result_list[0]
    assert "id" in first
    assert "title" in first
    assert "acceptance_end_datetime" in first
    assert "subsidy_max_limit" in first
    
    print(f"\n[Test Verification] Parsed {len(result_list)} real items successfully.")
    print(f"Sample 1: ID={first['id']}, Title={first['title']}, MaxAmount={first['subsidy_max_limit']}")
