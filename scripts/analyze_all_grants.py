"""
jGrants 全実データに対する地域別（全国・富山・各都道府県）集計・分析スクリプト (analyze_all_grants.py)
"""

import json
from collections import Counter
from pathlib import Path

def analyze_grants():
    snapshot_path = Path(".cache/snapshots/jgrants_real_sample.json")
    if not snapshot_path.exists():
        print("Snapshot file not found.")
        return

    with open(snapshot_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("result", [])
    print(f"Total Grants Analyzed: {len(items)} 件\n")

    area_counter = Counter()
    toyama_list = []
    national_list = []

    for item in items:
        area = item.get("target_area_search") or "未設定/全国"
        area_counter[area] += 1
        
        title = item.get("title", "")
        if "富山" in area or "富山" in title:
            toyama_list.append(item)
        if "全国" in area or area == "未設定/全国":
            national_list.append(item)

    print("==================== 地域別件数ランキング ====================")
    for area, count in area_counter.most_common(15):
        print(f"  ・{area}: {count} 件")

    print(f"\n==================== 【全国対象】({len(national_list)} 件のサンプル) ====================")
    for i, item in enumerate(national_list[:5], 1):
        print(f"[{i}] {item.get('title')}")
        print(f"    対象地域: {item.get('target_area_search')}")
        print(f"    公募締切: {item.get('acceptance_end_datetime')}")
        print(f"    助成上限: {item.get('subsidy_max_limit')} 円")
        print("-" * 55)

    print(f"\n==================== 【富山県対象/関連】({len(toyama_list)} 件) ====================")
    if toyama_list:
        for i, item in enumerate(toyama_list, 1):
            print(f"[{i}] {item.get('title')}")
            print(f"    対象地域: {item.get('target_area_search')}")
            print(f"    公募締切: {item.get('acceptance_end_datetime')}")
            print(f"    助成上限: {item.get('subsidy_max_limit')} 円")
            print("-" * 55)
    else:
        print("  ※ 現在 jGrants 上で「富山県」が明示された単独公募は 0 件（全国対象公募が適用可能）。")

if __name__ == "__main__":
    analyze_grants()
