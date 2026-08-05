#!/usr/bin/env python3
"""
sync_grants_cron.py — jGrants 差分同期 & ステータス更新 Cron スクリプト
(grant_pipeline_spec.md §2 `grants_sync_engine`)

毎日 01:00 UTC 等で定期実行し、以下を自動化する:
  1. jGrants API `/subsidies` (acceptance=1) から「募集中」助成金 ID の全集合 S_api を取得
     (キーワード未指定で 0 件になる仕様のため、主要 4 語で横断巡回して重複排除)
  2. DB 内 source='jgrants' & status='OPEN' の ID 集合 S_db と比較
  3. S_db - S_api (API に存在しなくなった) を status='CLOSED' へ一括更新
  4. S_api 全件の詳細を取得し public.grants へ ON CONFLICT で Upsert

再利用: search_jgrants.py の fetch_detail / save_grants_to_db を import する。

usage:
  env -u PYTHONPATH uv run skills/jgrants_search/scripts/sync_grants_cron.py           # 実行
  env -u PYTHONPATH uv run skills/jgrants_search/scripts/sync_grants_cron.py --dry-run # 変更せず集計のみ
"""

import argparse
import asyncio
import json
import os
import sys
import re
from pathlib import Path
from typing import Any, Optional, Set

import httpx
from dotenv import load_dotenv

# 同一ディレクトリの search_jgrants.py を再利用
sys.path.insert(0, str(Path(__file__).resolve().parent))
from search_jgrants import (  # noqa: E402
    JGRANTS_LIST_API,
    JGRANTS_DETAIL_API,
    RATE_10_10_PATTERNS,
    ADVANCE_PATTERNS,
    sanitize_text,
    fetch_detail,
    save_grants_to_db,
)

env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")

# キーワード未指定時 0 件問題を回避する主要横断キーワード
SWEEP_KEYWORDS = ["事業", "補助金", "助成金", "支援"]


def build_result(item: dict, detail: dict) -> Optional[dict]:
    """search_jgrants.run_search と同じ結果 dict 形状に正規化する。"""
    target_detail = detail or item
    gid = target_detail.get("id", item.get("id"))
    title = target_detail.get("title", item.get("title"))
    if not gid or not title:
        return None
    subsidy_rate = target_detail.get("subsidy_rate", "") or ""
    detail_text = target_detail.get("detail", "") or ""
    is_10_10 = any(re.search(p, subsidy_rate, re.IGNORECASE) for p in RATE_10_10_PATTERNS)
    advance_text = f"{subsidy_rate} {detail_text}"
    is_advance = any(re.search(p, advance_text, re.IGNORECASE) for p in ADVANCE_PATTERNS)
    return {
        "id": gid,
        "title": title,
        "provider": target_detail.get("organization", item.get("organization", "デジタル庁/jGrants")),
        "subsidy_rate": subsidy_rate or "記載なし",
        "target_area": target_detail.get("target_area_search", "") or "全国",
        "min_amount": target_detail.get("subsidy_min_limit", item.get("subsidy_min_limit")),
        "max_amount": target_detail.get("subsidy_max_limit", item.get("subsidy_max_limit")),
        "deadline": target_detail.get("acceptance_end_datetime", "未設定"),
        "url": f"https://www.jgrants-portal.go.jp/subsidy/{gid}",
        "is_rate_10_10": is_10_10,
        "is_advance_payment": is_advance,
        "detail_text": detail_text,
        "raw_detail": target_detail,
    }


async def gather_accepting_ids(client: httpx.AsyncClient) -> Set[str]:
    """API 上の募集中 ID 全集合を横断巡回で取得 (重複排除)。"""
    seen: Set[str] = set()
    for kw in SWEEP_KEYWORDS:
        params = {"keyword": kw, "sort": "created_date", "order": "DESC", "acceptance": "1"}
        try:
            res = await client.get(JGRANTS_LIST_API, params=params)
            if res.status_code == 200:
                for item in res.json().get("result", []):
                    gid = item.get("id")
                    if gid:
                        seen.add(str(gid))
        except Exception as e:
            print(f"[WARN] keyword='{kw}' fetch failed: {e}")
        await asyncio.sleep(0.1)
    return seen


def mark_closed(ids: Set[str], dry_run: bool = False) -> int:
    """API 上に消えた ID (S_db - S_api) を CLOSED に更新。"""
    if not ids:
        return 0
    if dry_run:
        print(f"[DRY] {len(ids)} grants would be marked CLOSED: {sorted(ids)[:10]}{'...' if len(ids) > 10 else ''}")
        return len(ids)
    try:
        import psycopg
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.grants SET status='CLOSED', updated_at=NOW() "
                    "WHERE source='jgrants' AND status='OPEN' AND source_grant_id = ANY(%s);",
                    (list(ids),),
                )
                n = cur.rowcount
            conn.commit()
        print(f"[DB] Marked {n} grants as CLOSED (募集終了).")
        return n
    except Exception as e:
        print(f"[ERROR] mark_closed failed: {e}")
        return 0


async def main(dry_run: bool = False) -> None:
    if not DATABASE_URL:
        print("[ERROR] DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    import psycopg
    import psycopg.rows

    headers = {"User-Agent": "AutoGrantsBot/1.0", "Accept": "application/json"}

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        print("=== jGrants 差分同期開始 ===")

        # 1. API 上の募集中 ID 集合
        api_ids = await gather_accepting_ids(client)
        print(f"[API] 募集中助成金 ID 集合: {len(api_ids)} 件")

        if not api_ids:
            print("[WARN] API から取得できず。中断します。")
            return

        # 2. DB の OPEN 集合
        with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
            rows = conn.execute(
                "SELECT source_grant_id FROM public.grants WHERE source='jgrants' AND status='OPEN';"
            ).fetchall()
            db_ids = {str(r["source_grant_id"]) for r in rows}
        print(f"[DB ] source='jgrants' & OPEN 集合: {len(db_ids)} 件")

        # 3. 募集終了 (CLOSED) 化
        to_close = db_ids - api_ids
        if to_close:
            print(f"[SYNC] 募集終了と判定: {len(to_close)} 件 → CLOSED 化")
            mark_closed(to_close, dry_run=dry_run)
        else:
            print("[SYNC] 募集終了候補なし")

        # 4. 詳細取得 & Upsert
        ids_list = sorted(api_ids)
        results = []
        # 既存レコードの有無で「新規/更新」を集計
        with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
            existing = {
                str(r["source_grant_id"])
                for r in conn.execute(
                    "SELECT source_grant_id FROM public.grants WHERE source='jgrants';"
                ).fetchall()
            }
        new_ids = api_ids - existing

        batch_size = 5
        for i in range(0, len(ids_list), batch_size):
            batch = ids_list[i : i + batch_size]
            details = await asyncio.gather(*[fetch_detail(client, g) for g in batch])
            for item_lite, detail in zip(batch, details):
                # リスト API の item が無いので、gid から最小 item dict を作る
                pseudo = {"id": item_lite}
                res = build_result(pseudo, detail)
                if res:
                    results.append(res)
            await asyncio.sleep(0.1)

        print(f"[FETCH] 詳細取得 & 正規化: {len(results)} 件 (新規: {len(new_ids & api_ids)} 含む)")

        if dry_run:
            print(f"[DRY] Upsert 対象 {len(results)} 件 / CLOSED 対象 {len(to_close)} 件 (変更なし)")
            return

        saved = save_grants_to_db(results)
        print("=== 差分同期完了 ===")
        print(f"    - Upsert 保存: {saved} 件")
        print(f"    - CLOSED 化 : {len(to_close)} 件")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="jGrants 差分同期 Cron")
    parser.add_argument("--dry-run", action="store_true", help="DB を変更せず集計のみ表示")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
