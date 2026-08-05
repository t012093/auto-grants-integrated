#!/usr/bin/env python3
"""
Seed Yaotsu Proposal Script (seed_yaotsu_proposal.py)

NPO法人 Open Coral Network の「富山市旧八尾町×農水省500万円定額事業」の
実企画書データを Neon DB (grant_proposals, proposal_grant_mappings, proposal_project_offers) に登録する。
"""

import os
import sys
import json
import logging
from pathlib import Path
import psycopg
import psycopg.rows
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")


def main():
    if not DATABASE_URL:
        logger.error("DATABASE_URL が設定されていません")
        sys.exit(1)

    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            # 1. Open Coral Network の npo_id を取得
            cur.execute("SELECT id, name FROM public.npo_profiles WHERE name LIKE '%Open Coral%' LIMIT 1;")
            npo = cur.fetchone()
            if not npo:
                logger.error("NPO Open Coral Network がプロファイルに存在しません")
                sys.exit(1)

            npo_id = npo["id"]
            logger.info(f"Target NPO: {npo['name']} ({npo_id})")

            # 2. 農水省 中山間地域所得確保推進事業 の grant_id を検索/取得
            cur.execute("SELECT id, title, amount_max FROM public.grants WHERE title LIKE '%中山間%' OR title LIKE '%所得確保%' LIMIT 1;")
            grant = cur.fetchone()
            grant_id = grant["id"] if grant else None
            grant_title = grant["title"] if grant else "中山間地域所得確保推進事業"
            logger.info(f"Target Grant: {grant_title} (ID: {grant_id})")

            # 3. 企画書データ作成 (grant_proposals)
            title = "【農水省500万】富山市旧八尾町 AI/IT教育×子ども食堂×地域農産物ECモデル"
            concept_summary = (
                "大長谷の特産品（エゴマ・山菜・そば等）をNPOが買い取り、子ども食堂×AIプログラミング教室で活用しつつ、"
                "自社Webメディア（Coral Magazine）で全国EC販売し、売上を農家へ還元する手出しゼロ（定額500万・概算払い可）プロジェクト。"
            )

            budget_json = {
                "人件費・作業手当": 1800000,
                "食材買い取り代": 1000000,
                "EC開発・PRデザイン外注": 1200000,
                "会場費・備品購入": 700000,
                "旅費・交通費": 300000
            }

            kpi_json = {
                "子ども食堂参加者": 300,
                "AI教室受講者": 80,
                "農作物の買い取り総額": 1000000,
                "EC全国売上目標": 1500000
            }

            content_markdown = f"""# 詳細事業計画書: {title}

## 1. 地域の課題と背景
富山市旧八尾町（特に大長谷エリア）は、人口減少と超高齢化が進み、農家の所得低下と地域の孤立化が深刻な課題となっています。本事業は、地元の農家さん2名および現地NPO（白木峰と大長谷を愛する会）と連携し、地域農産物の高付加価値化と子どもの居場所づくりを同時に達成します。

## 2. コア事業内容
1. **子ども食堂 ＆ AIプログラミング教室の開催**: 大長谷の新鮮な食材を使った子ども食堂と、最新AI・IT技術を楽しく学ぶ教室を定期開催。
2. **地域特産品の開発 ＆ Coral Magazine EC販売**: 農家さんから直接エゴマや山菜等を買い取り、特産品パッケージ化して全国へWeb販売。売上を農家さんへ還元。
3. **大長谷地域所得確保協議会の運営**: 農家2名・現地NPO・Open Coral Networkが一体となった自律型運営。

## 3. 500万円予算使途明細
- **人件費・作業手当 (180万円)**: NPOスタッフ・役員の作業手当（タイムシート管理）
- **食材買い取り代 (100万円)**: 地元農家からの直販購入費
- **EC開発・PRデザイン (120万円)**: Coral MagazineのEC改修・ロゴパッケージデザイン
- **会場費・備品代 (70万円)**: パソコン/タブレット・調理器具購入、八尾拠点・集会所賃料
- **旅費・交通費 (30万円)**: 富山市街地〜大長谷間のガソリン代・レンタカー代

## 4. 概算払い（前払い）および手出し0円運用
採択後直ちに概算払請求書を提出し、事業開始に必要な資金の交付を受けることで、NPO側での500万円の立て替えを一切不要とします。
"""

            # インサート実行 (ON CONFLICT更新)
            cur.execute(
                """
                INSERT INTO public.grant_proposals (
                    npo_id, title, concept_summary, status, content_markdown, budget_json, kpi_json
                ) VALUES (%s, %s, %s, 'PARTNER_MATCHING', %s, %s, %s)
                RETURNING id;
                """,
                (npo_id, title, concept_summary, content_markdown, json.dumps(budget_json), json.dumps(kpi_json))
            )
            proposal_id = cur.fetchone()["id"]
            logger.info(f"✅ Created proposal_id: {proposal_id}")

            # 4. 助成金マッピング作成 (proposal_grant_mappings)
            if grant_id:
                cur.execute(
                    """
                    INSERT INTO public.proposal_grant_mappings (
                        proposal_id, grant_id, is_primary, match_score, status, notes
                    ) VALUES (%s, %s, TRUE, 83, 'CONSIDERING', '農水省500万円定額事業（本命）')
                    ON CONFLICT (proposal_id, grant_id) DO NOTHING;
                    """,
                    (proposal_id, grant_id)
                )

            # 5. ポジションオファー作成 (proposal_project_offers)
            offers = [
                ("PM", "プロジェクトリーダー (PM)", 1, "HYBRID", "時給2,000円 × 200h = 40万円", json.dumps(["富山市役所 農政企画課との事前相談", "500万円予算進捗管理", "申請書の最終承認"])),
                ("LOCAL_DIR", "地域・農家連携ディレクター", 1, "HUMAN", "時給1,500円 × 100h = 15万円", json.dumps(["八尾・大長谷農家2名との対面面談", "大長谷協議会 同意書(A4 1枚)の署名獲得", "現地集会所の手配"])),
                ("SITE_OP", "現場運営・子ども食堂担当", 2, "HUMAN", "時給1,200円 × 80h = 約10万円/人", json.dumps(["子ども食堂＆AI教室の会場準備", "当日の受付・調理補助", "食材受け取り管理"])),
                ("IT_CREATOR", "IT/EC・広報クリエイター", 2, "HYBRID", "成果報酬・手当 15万円/人", json.dumps(["Coral Magazine 特産品EC販売ページの構築", "特産品パッケージおよびPRチラシのデザイン制作"])),
            ]

            for code, name, cap, tag, notes, tasks_json in offers:
                cur.execute(
                    """
                    INSERT INTO public.proposal_project_offers (
                        proposal_id, position_code, position_name, capacity, task_allocation_tag, compensation_notes, initial_tasks_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """,
                    (proposal_id, code, name, cap, tag, notes, tasks_json)
                )

            conn.commit()
            logger.info("✅ Proposal seeding completed successfully!")
            print(f"\n🎉 登録完了 Proposal ID: {proposal_id}")


if __name__ == "__main__":
    main()
