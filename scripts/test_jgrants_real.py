"""
jGrants 公式 API 実データ取得・検証スクリプト (test_jgrants_real.py)
"""

import asyncio
import json
from pathlib import Path
import httpx

JGRANTS_PUBLIC_API_URL = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies"

async def fetch_jgrants_real_data():
    print(f"Connecting to Digital Agency jGrants Official API: {JGRANTS_PUBLIC_API_URL} ...")
    
    headers = {
        "User-Agent": "AutoGrantsBot/1.0 (Civic Grant Intelligence)",
        "Accept": "application/json"
    }
    
    # 検索パラメータ: 募集中 (acceptance=1) かつ "NPO" または "地域" 関連
    params = {
        "keyword": "地域",
        "sort": "created_date",
        "order": "DESC",
        "acceptance": "1"  # 募集中のみ
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            response = await client.get(JGRANTS_PUBLIC_API_URL, params=params, headers=headers)
            print(f"Response Status Code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print("SUCCESS: Real jGrants data received!")
                
                # スナップショットの保存 (.cache/snapshots/jgrants_real_sample.json)
                snapshot_dir = Path(".cache/snapshots")
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                snapshot_file = snapshot_dir / "jgrants_real_sample.json"
                
                with open(snapshot_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                print(f"Real Data Snapshot saved to: {snapshot_file}")
                
                # アイテム抽出
                result_list = data.get("result", []) if isinstance(data, dict) else data
                if not result_list and isinstance(data, dict):
                    result_list = data.get("subsidies", [])
                
                print(f"\n==================== Total Items: {len(result_list)} ====================")
                for i, item in enumerate(result_list[:5], 1):
                    title = item.get("title", item.get("name", "名称不明"))
                    subsidizer = item.get("subsidizer", item.get("target_name", item.get("agency", "デジタル庁/官庁")))
                    deadline = item.get("acceptance_end_datetime", item.get("end_date", "未設定"))
                    url = item.get("target_url", item.get("detail_url", item.get("id", "N/A")))
                    max_limit = item.get("subsidy_max_limit", item.get("max_amount", "記載なし"))
                    
                    print(f"[{i}] {title}")
                    print(f"    管轄・提供: {subsidizer}")
                    print(f"    公募締切  : {deadline}")
                    print(f"    助成上限  : {max_limit}")
                    print(f"    参照 URL  : {url}")
                    print("-" * 55)
            else:
                print(f"FAILED with Status {response.status_code}")
                print(f"Error Body: {response.text[:500]}")

        except Exception as e:
            print(f"Connection Error: {e}")

if __name__ == "__main__":
    asyncio.run(fetch_jgrants_real_data())
