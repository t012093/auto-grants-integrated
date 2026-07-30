"""
jGrants API リアル通信テストスクリプト (test_jgrants_api.py)
"""

import asyncio
import json
from pathlib import Path
import httpx

JGRANTS_API_URL = "https://api.jgrants-portal.go.jp/v1/subsidy/list"

async def test_jgrants_api():
    print(f"Fetching from jGrants API: {JGRANTS_API_URL} ...")
    headers = {
        "User-Agent": "AutoGrantsBot/1.0 (Civic Grant Intelligence)",
        "Accept": "application/json"
    }
    
    # 検索キーワードなどのクエリパラメータを設定
    params = {
        "keyword": "NPO",
        "sort": "created_date",
        "order": "DESC"
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            response = await client.get(JGRANTS_API_URL, params=params, headers=headers)
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print("API call successful!")
                
                # スナップショットの保存
                snapshot_dir = Path(".cache/snapshots")
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                snapshot_file = snapshot_dir / "jgrants_sample.json"
                
                with open(snapshot_file, "w", encoding="utf-8") as f:
                    json.dumps(data, f, ensure_ascii=False, indent=2)
                
                print(f"Snapshot saved to: {snapshot_file}")
                
                # サンプルデータの要約表示
                result_list = data.get("result", [])
                if not result_list and isinstance(data, list):
                    result_list = data
                
                print(f"Total Items Retrieved: {len(result_list)}")
                print("\n--- Sample Grants (Top 3) ---")
                for i, item in enumerate(result_list[:3], 1):
                    print(f"[{i}] {item.get('title', 'No Title')}")
                    print(f"    Provider: {item.get('subsidizer', item.get('target_name', 'N/A'))}")
                    print(f"    Deadline: {item.get('acceptance_end_datetime', item.get('deadline', 'N/A'))}")
                    print(f"    URL: {item.get('target_url', item.get('front_subsidy_detail_page_url', 'N/A'))}")
                    print("-" * 40)
            else:
                print(f"API Returned Non-200 Status: {response.status_code}")
                print(f"Response Body: {response.text[:500]}")

        except Exception as e:
            print(f"Error connecting to jGrants API: {e}")

if __name__ == "__main__":
    asyncio.run(test_jgrants_api())
