"""
「全国対象」および「富山県対象」の助成金・公募情報調査スクリプト (search_grants_by_area.py)
"""

import asyncio
import json
from pathlib import Path
import httpx

JGRANTS_PUBLIC_API_URL = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies"

async def fetch_grants(keyword: str = "", area: str = "", acceptance: str = "1") -> list:
    headers = {
        "User-Agent": "AutoGrantsBot/1.0 (Civic Grant Intelligence)",
        "Accept": "application/json"
    }
    params = {
        "sort": "created_date",
        "order": "DESC",
        "acceptance": acceptance
    }
    if keyword:
        params["keyword"] = keyword
    if area:
        params["target_area_search"] = area

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            res = await client.get(JGRANTS_PUBLIC_API_URL, params=params, headers=headers)
            if res.status_code == 200:
                data = res.json()
                items = data.get("result", []) if isinstance(data, dict) else data
                return items
            else:
                print(f"Error fetching (Status {res.status_code}): {res.text[:200]}")
                return []
        except Exception as e:
            print(f"Exception: {e}")
            return []

def print_grants_summary(title: str, grants: list, max_display: int = 5):
    print(f"\n==================== 【{title}】 該当件数: {len(grants)} 件 ====================")
    for i, item in enumerate(grants[:max_display], 1):
        g_title = item.get("title", "名称不明")
        area = item.get("target_area_search", "全国/未設定")
        deadline = item.get("acceptance_end_datetime", "未設定")
        max_amount = item.get("subsidy_max_limit", "記載なし")
        gid = item.get("id", "")
        url = f"https://www.jgrants-portal.go.jp/subsidy/{gid}" if gid else "N/A"
        
        print(f"[{i}] {g_title}")
        print(f"    対象地域: {area}")
        print(f"    公募締切: {deadline}")
        print(f"    助成上限: {max_amount} 円" if isinstance(max_amount, (int, float)) else f"    助成上限: {max_amount}")
        print(f"    詳細 URL: {url}")
        print("-" * 65)

async def main():
    print("デジタル庁 jGrants API より「富山県対象」および「全国対象」の助成金・公募を検索・解析中...\n")

    # 1. 富山県対象の検索
    toyama_grants = await fetch_grants(keyword="富山")
    
    # 2. 全国対象の検索
    national_grants = await fetch_grants(keyword="全国")
    
    # 3. リモート・地域NPO全般の検索
    npo_grants = await fetch_grants(keyword="NPO")

    # 結果表示
    print_grants_summary("富山県対象・関連の助成金・公募", toyama_grants, max_display=10)
    print_grants_summary("全国対象の助成金・公募", national_grants, max_display=10)
    print_grants_summary("NPO・市民活動対象の助成金・公募", npo_grants, max_display=5)

    # スナップショット保存
    snapshot_dir = Path(".cache/snapshots")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    
    with open(snapshot_dir / "grants_toyama.json", "w", encoding="utf-8") as f:
        json.dump(toyama_grants, f, ensure_ascii=False, indent=2)
        
    with open(snapshot_dir / "grants_national.json", "w", encoding="utf-8") as f:
        json.dump(national_grants, f, ensure_ascii=False, indent=2)
        
    print("\nSnapshots saved to .cache/snapshots/grants_toyama.json and grants_national.json")

if __name__ == "__main__":
    asyncio.run(main())
