"""
jGrants API エンドポイント探索スクリプト (test_jgrants_endpoints.py)
"""

import asyncio
import httpx

ENDPOINTS = [
    "https://api.jgrants-portal.go.jp/exp/v1/subsidy/list",
    "https://api.jgrants-portal.go.jp/v1/subsidy",
    "https://jgrants-portal.go.jp/api/v1/subsidy",
    "https://jgrants-portal.go.jp/api/v1/subsidy/list",
    "https://api.jgrants-portal.go.jp/exp/v1/subsidy",
]

async def test_endpoints():
    headers = {
        "User-Agent": "AutoGrantsBot/1.0",
        "Accept": "application/json"
    }
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for url in ENDPOINTS:
            try:
                res = await client.get(url, params={"keyword": "補助金"}, headers=headers)
                print(f"[{res.status_code}] {url}")
                if res.status_code == 200:
                    print("--> SUCCESS! Response snippet:", res.text[:200])
            except Exception as e:
                print(f"[ERR] {url}: {e}")

if __name__ == "__main__":
    asyncio.run(test_endpoints())
