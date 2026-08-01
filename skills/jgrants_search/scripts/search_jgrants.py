#!/usr/bin/env python3
"""
jGrants 汎用検索 & 条件フィルタリング CLI スクリプト (search_jgrants.py)

デジタル庁 jGrants 公式 API に接続し、指定された条件
(--keyword, --area, --rate-10-10, --advance-payment, --min-amount, --max-amount, --limit, --json)
に基づいて助成金・公募情報を検索・抽出します。
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional
import httpx
from dotenv import load_dotenv

# Load .env
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")

JGRANTS_LIST_API = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies"
JGRANTS_DETAIL_API = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies/id"

RATE_10_10_PATTERNS = [r"10/10", r"10分の10", r"１０／１０", r"１０分の１０", r"定額", r"全額補助", r"100%"]
ADVANCE_PATTERNS = [r"概算払", r"前払", r"前金", r"事前交付"]


def sanitize_text(text: Any) -> str:
    """PostgreSQL の TEXT / JSONB 保存でエラーとなる NUL 文字 (\x00) を自動除去する"""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return text.replace("\x00", "")


def parse_safe_date(raw_date: Any) -> Optional[str]:
    """日付フォーマットの揺れや例外文字列に対応する安全な日付パース関数"""
    if not raw_date or raw_date in ("未設定", "なし", "None"):
        return None
    try:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", str(raw_date))
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def save_grants_to_db(grants_data: list[dict]) -> int:
    """検索結果を public.grants テーブルへ ON CONFLICT (source, source_grant_id) DO UPDATE で安全に保存・更新する。

    - 二重登録を 100% 防止
    - is_ocr_processed などの既存設定を破滅させずに動的カラムのみ上書き
    - ヌル文字除去 (\x00) & 安全な日付パース対応
    - 不完全データは自動スキップ
    """
    if not DATABASE_URL:
        print("[ERROR] DATABASE_URL is not set in environment. Skipping DB save.")
        return 0

    try:
        import psycopg
    except ImportError:
        print("[ERROR] psycopg library is missing. Install with 'uv add psycopg'. Skipping DB save.")
        return 0

    valid_items = []
    for item in grants_data:
        gid = item.get("id")
        title = item.get("title")
        if not gid or not title:
            print(f"[WARN] Skipping invalid item without id or title: {item}")
            continue
        valid_items.append(item)

    if not valid_items:
        print("[INFO] No valid items to save to DB.")
        return 0

    upsert_sql = """
    INSERT INTO public.grants (
        source,
        source_grant_id,
        title,
        provider,
        amount_min,
        amount_max,
        deadline,
        details_url,
        target_area,
        attachment_urls,
        is_rate_10_10,
        is_advance_payment,
        detail_text,
        payload_json,
        status,
        updated_at
    ) VALUES (
        'jgrants',
        %(id)s,
        %(title)s,
        %(provider)s,
        %(amount_min)s,
        %(amount_max)s,
        %(deadline)s,
        %(url)s,
        %(target_area)s,
        %(attachment_urls)s,
        %(is_rate_10_10)s,
        %(is_advance_payment)s,
        %(detail_text)s,
        %(payload_json)s,
        'OPEN',
        NOW()
    )
    ON CONFLICT (source, source_grant_id) DO UPDATE SET
        title = EXCLUDED.title,
        provider = EXCLUDED.provider,
        amount_min = EXCLUDED.amount_min,
        amount_max = EXCLUDED.amount_max,
        deadline = EXCLUDED.deadline,
        details_url = EXCLUDED.details_url,
        target_area = EXCLUDED.target_area,
        attachment_urls = EXCLUDED.attachment_urls,
        is_rate_10_10 = EXCLUDED.is_rate_10_10,
        is_advance_payment = EXCLUDED.is_advance_payment,
        detail_text = EXCLUDED.detail_text,
        payload_json = EXCLUDED.payload_json,
        status = EXCLUDED.status,
        updated_at = NOW();
    """

    saved_count = 0
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                for item in valid_items:
                    # 安全な日付パース
                    deadline_val = parse_safe_date(item.get("deadline"))

                    max_amt = item.get("max_amount")
                    amount_max_val = int(max_amt) if isinstance(max_amt, (int, float)) else None

                    min_amt = item.get("min_amount")
                    amount_min_val = int(min_amt) if isinstance(min_amt, (int, float)) else None

                    raw_detail = item.get("raw_detail") or {}

                    # 添付ファイル URL の抽出
                    attachments = []
                    for key in ["front_submittal_file", "pdf_url", "outline_file", "submittal_file"]:
                        val = raw_detail.get(key)
                        if isinstance(val, str) and val.startswith("http"):
                            attachments.append(sanitize_text(val))
                        elif isinstance(val, list):
                            for v in val:
                                if isinstance(v, str) and v.startswith("http"):
                                    attachments.append(sanitize_text(v))
                                elif isinstance(v, dict) and v.get("url"):
                                    attachments.append(sanitize_text(v["url"]))

                    params = {
                        "id": sanitize_text(item["id"]),
                        "title": sanitize_text(item["title"]),
                        "provider": sanitize_text(item.get("provider") or "デジタル庁/jGrants"),
                        "amount_min": amount_min_val,
                        "amount_max": amount_max_val,
                        "deadline": deadline_val,
                        "url": sanitize_text(item.get("url") or f"https://www.jgrants-portal.go.jp/subsidy/{item['id']}"),
                        "target_area": sanitize_text(item.get("target_area") or "全国"),
                        "attachment_urls": attachments,
                        "is_rate_10_10": bool(item.get("is_rate_10_10", False)),
                        "is_advance_payment": bool(item.get("is_advance_payment", False)),
                        "detail_text": sanitize_text(item.get("detail_text") or ""),
                        "payload_json": sanitize_text(json.dumps(raw_detail, ensure_ascii=False)),
                    }
                    cur.execute(upsert_sql, params)
                    saved_count += 1
            conn.commit()
        print(f"[DB] Successfully upserted {saved_count} grants to public.grants.")
        return saved_count
    except Exception as e:
        print(f"[ERROR] Failed to save grants to DB after {saved_count} items: {e}")
        return 0


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


async def run_search(
    keyword: str,
    area: str,
    rate_10_10: bool,
    advance_payment: bool,
    min_amount: int,
    max_amount: int,
    limit: int,
    output_json: bool,
    save_db: bool = False,
):
    headers = {"User-Agent": "AutoGrantsBot/1.0", "Accept": "application/json"}

    # キーワード横断取得ロジック
    search_keywords = [keyword] if keyword else ["事業", "補助金", "助成金", "支援"]
    list_items = []
    seen_ids = set()

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        for kw in search_keywords:
            params = {"keyword": kw, "sort": "created_date", "order": "DESC", "acceptance": "1"}
            res = await client.get(JGRANTS_LIST_API, params=params)
            if res.status_code == 200:
                for item in res.json().get("result", []):
                    gid = item.get("id")
                    if gid and gid not in seen_ids:
                        seen_ids.add(gid)
                        list_items.append(item)

        if not list_items:
            # スナップショットフォールバック
            snapshot_path = Path(".cache/snapshots/jgrants_real_sample.json")
            if snapshot_path.exists():
                with open(snapshot_path, "r", encoding="utf-8") as f:
                    list_items = json.load(f).get("result", [])

        if not list_items:
            if output_json:
                print("[]")
            else:
                print("No items retrieved from jGrants API.")
            return

        results = []
        batch_size = 5
        for i in range(0, len(list_items), batch_size):
            batch = list_items[i : i + batch_size]
            details = await asyncio.gather(*[fetch_detail(client, item.get("id")) for item in batch])

            for item, detail in zip(batch, details):
                target_detail = detail or item
                g_area = target_detail.get("target_area_search", "") or "全国"
                subsidy_rate = target_detail.get("subsidy_rate", "") or ""
                detail_text = target_detail.get("detail", "") or ""
                combined_text = f"{subsidy_rate} {detail_text}"

                is_10_10 = any(re.search(p, combined_text, re.IGNORECASE) for p in RATE_10_10_PATTERNS)
                is_advance = any(re.search(p, combined_text, re.IGNORECASE) for p in ADVANCE_PATTERNS)

                # エリアフィルター
                if area and area not in g_area and "全国" not in g_area:
                    continue

                # 10/10 フィルター
                if rate_10_10 and not is_10_10:
                    continue

                # 前払い/概算払いフィルター
                if advance_payment and not is_advance:
                    continue

                # 金額フィルター
                max_limit_val = target_detail.get("subsidy_max_limit", item.get("subsidy_max_limit"))
                if min_amount is not None or max_amount is not None:
                    if not isinstance(max_limit_val, (int, float)):
                        continue
                    if min_amount is not None and max_limit_val < min_amount:
                        continue
                    if max_amount is not None and max_limit_val > max_amount:
                        continue

                gid = target_detail.get("id", item.get("id"))
                min_limit_val = target_detail.get("subsidy_min_limit", item.get("subsidy_min_limit"))
                results.append({
                    "id": gid,
                    "title": target_detail.get("title", item.get("title")),
                    "provider": target_detail.get("organization", item.get("organization", "デジタル庁/jGrants")),
                    "subsidy_rate": subsidy_rate or "記載なし",
                    "target_area": g_area,
                    "min_amount": min_limit_val,
                    "max_amount": max_limit_val if max_limit_val is not None else "記載なし",
                    "deadline": target_detail.get("acceptance_end_datetime", "未設定"),
                    "url": f"https://www.jgrants-portal.go.jp/subsidy/{gid}",
                    "is_rate_10_10": is_10_10,
                    "is_advance_payment": is_advance,
                    "detail_text": detail_text,
                    "raw_detail": target_detail,
                })

                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
            await asyncio.sleep(0.1)

        # --save-db フラグ指定時は DB へ保存
        if save_db and results:
            save_grants_to_db(results)

        if output_json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return

        # 結果表示 (Formatted Text)
        print(f"=== jGrants 検索結果 (該当: {len(results)} 件) ===")
        for i, r in enumerate(results, 1):
            print(f"[{i}] {r['title']}")
            print(f"    補助率  : {r['subsidy_rate']}")
            print(f"    対象地域: {r['target_area']}")
            print(
                f"    助成上限: {r['max_amount']} 円"
                if isinstance(r["max_amount"], (int, float))
                else f"    助成上限: {r['max_amount']}"
            )
            print(f"    公募締切: {r['deadline']}")
            print(f"    詳細 URL: {r['url']}")
            print("-" * 60)


def main():
    parser = argparse.ArgumentParser(description="jGrants 公式 API 条件検索 CLI")
    parser.add_argument("--keyword", type=str, default="", help="検索キーワード (未指定時は主要語で自動横断取得)")
    parser.add_argument("--area", type=str, default="", help="対象地域 (例: 富山県, 東京都, 全国)")
    parser.add_argument("--rate-10-10", action="store_true", help="補助率 10/10 (全額補助・定額) のみに絞り込む")
    parser.add_argument("--advance-payment", action="store_true", help="概算払い・前払い記載のあるものに絞り込む")
    parser.add_argument("--min-amount", type=int, default=None, help="助成上限額の下限 (円)")
    parser.add_argument("--max-amount", type=int, default=None, help="助成上限額の上限 (円)")

    parser.add_argument("--limit", type=int, default=10, help="表示件数の上限")
    parser.add_argument("--save-db", action="store_true", help="検索結果を自社 DB (public.grants) に自動 Upsert 保存")
    parser.add_argument("--json", action="store_true", help="JSON 形式で結果を出力")

    args = parser.parse_args()
    asyncio.run(
        run_search(
            args.keyword,
            args.area,
            args.rate_10_10,
            args.advance_payment,
            args.min_amount,
            args.max_amount,
            args.limit,
            args.json,
            args.save_db,
        )
    )


if __name__ == "__main__":
    main()

