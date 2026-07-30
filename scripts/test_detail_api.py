"""
jGrants 補助金詳細 API 取得スクリプト (test_detail_api.py)
"""

import asyncio
import json
from pathlib import Path
import httpx

JGRANTS_DETAIL_API_URL = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies/id"

async def test_detail():
    # サンプル ID として先ほどの徳島県および山形県の ID を使用
    sample_ids = ["a0WJ200000CDdicMAD", "a0WJ200000CDddXMAT", "a0WJ200000CDdSBMA1"]
    
    headers = {"User-Agent": "AutoGrantsBot/1.0", "Accept": "application/json"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        for gid in sample_ids:
            url = f"{JGRANTS_DETAIL_API_URL}/{gid}"
            print(f"Fetching Detail API: {url}")
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                print("Detail Data Structure Keys:", list(data.keys()) if isinstance(data, dict) else "List")
                
                # 詳細データ内から「補助率」「概算払」「前金」に関するキーを探す
                result_obj = data.get("result", [{}])[0] if isinstance(data.get("result"), list) and data.get("result") else data
                
                print(f"Title: {result_obj.get('title')}")
                print(f"Subsidy Rate (補助率): {result_obj.get('subsidy_rate', result_obj.get('rate', '要詳細テキスト解析'))}")
                
                # 生データの先頭を保存
                snapshot_file = Path(f".cache/snapshots/detail_{gid}.json")
                with open(snapshot_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"Saved detail snapshot to {snapshot_file}\n")
            else:
                print(f"Failed Status: {res.status_code}\n")

if __name__ == "__main__":
    asyncio.run(test_detail())
