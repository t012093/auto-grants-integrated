#!/usr/bin/env python3
"""
jGrants 汎用検索 & 条件フィルタリング CLI スクリプト (search_jgrants.py)

デジタル庁 jGrants 公式 API に接続し、指定された条件
(--keyword, --area, --rate-10-10, --advance-payment, --limit)
に基づいて助成金・公募情報を検索・抽出します。
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
import httpx

JGRANTS_LIST_API = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies"
JGRANTS_DETAIL_API = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies/id"

RATE_10_10_PATTERNS = [r"10/10", r"10分の10", r"１０／１０", r"１０分の１０", r"定額", r"全額補助", r"100%"]
ADVANCE_PATTERNS = [r"概算払", r"前払", r"前金", r"事前交付"]

async def fetch_detail(client: httpx.AsyncClient, gid: str) -> dict:
    try:
        res = await client.get(f"{JGRANTS_DETAIL_API}/{gid}")
        if res.status_code == 200:
            data = res.json()
            result = data.get("result", [])
            return result[0] if isinstance(result, list) and result else (result if isinstance(result, dict) else {})
    except Exception:
        pass
    return {}

async def run_search(keyword: str, area: str, rate_10_10: bool, advance_payment: bool, limit: int):
    headers = {"User-Agent": "AutoGrantsBot/1.0", "Accept": "application/json"}
    params = {"keyword": keyword or "助成金", "sort": "created_date", "order": "DESC", "acceptance": "1"}

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        res = await client.get(JGRANTS_LIST_API, params=params)
        list_items = []
        if res.status_code == 200:
            list_items = res.json().get("result", [])
        else:
            # バックアップ・スナップショットフォールバック
            snapshot_path = Path(".cache/snapshots/jgrants_real_sample.json")
            if snapshot_path.exists():
                with open(snapshot_path, "r", encoding="utf-8") as f:
                    list_items = json.load(f).get("result", [])

        if not list_items:
            print("No items retrieved from jGrants API.")
            return

        results = []
        batch_size = 5
        for i in range(0, len(list_items), batch_size):
            batch = list_items[i:i+batch_size]
            details = await asyncio.gather(*[fetch_detail(client, item.get("id")) for item in batch])

            for item, detail in zip(batch, details):
                target_detail = detail or item
                g_area = target_detail.get("target_area_search", "") or "全国"
                subsidy_rate = target_detail.get("subsidy_rate", "") or ""
                detail_text = target_detail.get("detail", "") or ""
                combined_text = f"{subsidy_rate} {detail_text}"

                # エリアフィルター
                if area and area not in g_area and "全国" not in g_area:
                    continue

                # 10/10 フィルター
                if rate_10_10 and not any(re.search(p, combined_text) for p in RATE_10_10_PATTERNS):
                    continue

                # 前払い/概算払いフィルター
                if advance_payment and not any(re.search(p, combined_text) for p in ADVANCE_PATTERNS):
                    continue

                gid = target_detail.get("id", item.get("id"))
                results.append({
                    "id": gid,
                    "title": target_detail.get("title", item.get("title")),
                    "subsidy_rate": subsidy_rate or "記載なし",
                    "target_area": g_area,
                    "max_amount": target_detail.get("subsidy_max_limit", item.get("subsidy_max_limit", "記載なし")),
                    "deadline": target_detail.get("acceptance_end_datetime", "未設定"),
                    "url": f"https://www.jgrants-portal.go.jp/subsidy/{gid}"
                })

                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
            await asyncio.sleep(0.2)

        # 結果表示 (JSON & Formatted Text)
        print(f"=== jGrants 検索結果 (該当: {len(results)} 件) ===")
        for i, r in enumerate(results, 1):
            print(f"[{i}] {r['title']}")
            print(f"    補助率  : {r['subsidy_rate']}")
            print(f"    対象地域: {r['target_area']}")
            print(f"    助成上限: {r['max_amount']} 円" if isinstance(r['max_amount'], (int, float)) else f"    助成上限: {r['max_amount']}")
            print(f"    公募締切: {r['deadline']}")
            print(f"    詳細 URL: {r['url']}")
            print("-" * 60)

def main():
    parser = argparse.ArgumentParser(description="jGrants 公式 API 条件検索 CLI")
    parser.add_argument("--keyword", type=str, default="", help="検索キーワード (例: 地域, NPO, 子育て)")
    parser.add_argument("--area", type=str, default="", help="対象地域 (例: 富山県, 東京都, 全国)")
    parser.add_argument("--rate-10-10", action="store_true", help="補助率 10/10 (全額補助・定額) のみに絞り込む")
    parser.add_argument("--advance-payment", action="store_true", help="概算払い・前払い記載のあるものに絞り込む")
    parser.add_argument("--limit", type=int, default=10, help="表示件数の上限")

    args = parser.parse_args()
    asyncio.run(run_search(args.keyword, args.area, args.rate_10_10, args.advance_payment, args.limit))

if __name__ == "__main__":
    main()
