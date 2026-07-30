"""
全国および富山県対象の「補助率 10/10 (全額補助・定額)」助成金検索スクリプト (search_10_of_10_grants.py)
"""

import asyncio
import json
import re
from pathlib import Path
import httpx

JGRANTS_LIST_API = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies"
JGRANTS_DETAIL_API = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies/id"

RATE_10_10_PATTERNS = [
    r"10/10", r"10分の10", r"１０／１０", r"１０分の１０",
    r"定額", r"全額補助", r"100%"
]

async def fetch_grant_detail(client: httpx.AsyncClient, gid: str) -> dict:
    url = f"{JGRANTS_DETAIL_API}/{gid}"
    try:
        res = await client.get(url)
        if res.status_code == 200:
            data = res.json()
            result = data.get("result", [])
            if result and isinstance(result, list):
                return result[0]
            elif isinstance(result, dict):
                return result
    except Exception as e:
        print(f"Error fetching detail for {gid}: {e}")
    return {}

async def main():
    print("デジタル庁 jGrants 公式 API より募集中全案件を取得し、「10/10 (全額補助)」助成金を詳細解析中...\n")
    headers = {"User-Agent": "AutoGrantsBot/1.0", "Accept": "application/json"}

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        params = {
            "keyword": "助成金",
            "sort": "created_date",
            "order": "DESC",
            "acceptance": "1"
        }
        res = await client.get(JGRANTS_LIST_API, params=params)
        list_items = []
        if res.status_code == 200:
            list_items = res.json().get("result", [])
        else:
            print(f"API fetch fallback to snapshot file...")
            snapshot_path = Path(".cache/snapshots/jgrants_real_sample.json")
            if snapshot_path.exists():
                with open(snapshot_path, "r", encoding="utf-8") as f:
                    list_items = json.load(f).get("result", [])
        print(f"募集中全件数: {len(list_items)} 件。詳細データの並列取得と 10/10 判定を開始します...\n")

        matching_10_10_national = []
        matching_10_10_toyama = []

        # 並列処理で詳細 API を呼び出し (5件ずつバッチ)
        batch_size = 5
        for i in range(0, len(list_items), batch_size):
            batch = list_items[i:i+batch_size]
            tasks = [fetch_grant_detail(client, item.get("id")) for item in batch]
            details = await asyncio.gather(*tasks)

            for item, detail in zip(batch, details):
                if not detail:
                    continue
                
                subsidy_rate = detail.get("subsidy_rate", "") or ""
                detail_text = detail.get("detail", "") or ""
                combined_text = f"{subsidy_rate} {detail_text}"

                # 10/10 パターンチェック
                if any(re.search(p, combined_text) for p in RATE_10_10_PATTERNS):
                    area = detail.get("target_area_search", "") or "全国"
                    title = detail.get("title", item.get("title"))
                    max_amount = detail.get("subsidy_max_limit", item.get("subsidy_max_limit", "記載なし"))
                    deadline = detail.get("acceptance_end_datetime", "未設定")
                    gid = detail.get("id", item.get("id"))
                    url = f"https://www.jgrants-portal.go.jp/subsidy/{gid}"

                    match_data = {
                        "id": gid,
                        "title": title,
                        "area": area,
                        "subsidy_rate": subsidy_rate,
                        "max_amount": max_amount,
                        "deadline": deadline,
                        "url": url
                    }

                    if "富山" in area:
                        matching_10_10_toyama.append(match_data)
                    if "全国" in area or not area or area == "未設定":
                        matching_10_10_national.append(match_data)

            # レート制限に配慮して少しウェイト
            await asyncio.sleep(0.3)

        # 結果表示
        print(f"==================== 【全国対象 10/10 (全額補助) 助成金・公募】: {len(matching_10_10_national)} 件 ====================")
        for i, item in enumerate(matching_10_10_national, 1):
            print(f"[{i}] {item['title']}")
            print(f"    補助率 (10/10) : {item['subsidy_rate']}")
            print(f"    対象地域       : {item['area']}")
            print(f"    助成上限       : {item['max_amount']} 円" if isinstance(item['max_amount'], (int, float)) else f"    助成上限       : {item['max_amount']}")
            print(f"    公募締切       : {item['deadline']}")
            print(f"    詳細 URL       : {item['url']}")
            print("-" * 65)

        print(f"\n==================== 【富山県対象 10/10 (全額補助) 助成金・公募】: {len(matching_10_10_toyama)} 件 ====================")
        for i, item in enumerate(matching_10_10_toyama, 1):
            print(f"[{i}] {item['title']}")
            print(f"    補助率 (10/10) : {item['subsidy_rate']}")
            print(f"    対象地域       : {item['area']}")
            print(f"    助成上限       : {item['max_amount']} 円" if isinstance(item['max_amount'], (int, float)) else f"    助成上限       : {item['max_amount']}")
            print(f"    公募締切       : {item['deadline']}")
            print(f"    詳細 URL       : {item['url']}")
            print("-" * 65)

        # スナップショット保存
        snapshot_file = Path(".cache/snapshots/grants_10_of_10.json")
        snapshot_file.parent.mkdir(parents=True, exist_ok=True)
        with open(snapshot_file, "w", encoding="utf-8") as f:
            json.dump({
                "national": matching_10_10_national,
                "toyama": matching_10_10_toyama
            }, f, ensure_ascii=False, indent=2)
        print(f"\nResults saved to {snapshot_file}")

if __name__ == "__main__":
    asyncio.run(main())
