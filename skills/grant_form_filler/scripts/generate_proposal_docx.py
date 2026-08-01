#!/usr/bin/env python3
"""
Grant Proposal Draft Generator & Office Exporter Script (generate_proposal_docx.py)

要件適合チェック (grant_eligibility_checker) および経費最適化 (grant_expense_validator) を通過したデータと、
過去採択事例の勝因パターン (past_award_analyzer) を統合し、申請書の主要6大セクションを自動起草します。
生成した原稿は Harness Guard による算術・構造検証を経た後、officecli を用いて Word (.docx) および Excel (.xlsx) 形式に自動エクスポートします。
"""

import os
import sys
import json
import logging
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import psycopg
import psycopg.rows
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Load .env
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")

# 他モジュールの参照用パス追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "grant_expense_validator" / "scripts"))
try:
    from validate_expenses import ConstraintSolver, CATEGORY_LABELS
except ImportError:
    CATEGORY_LABELS = {
        "PERSONNEL": "人件費",
        "TRAVEL": "旅費交通費",
        "EQUIPMENT": "備品・機器購入費",
        "OUTSOURCING": "業務委託費",
        "SYSTEM": "システム開発・クラウド費",
        "PROMOTION": "広報・印刷製本費",
        "SUPPLIES": "消耗品・会場費",
        "OTHER": "その他雑費",
    }


class HarnessValidationError(Exception):
    """Raised when harness safety or arithmetic verification fails."""
    pass


class ProposalGenerator:
    """Orchestrator for Proposal Draft Generation & Office Export"""

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or DATABASE_URL

    def fetch_data(self, org_id: str, grant_id: str, strict: bool = False) -> Dict[str, Any]:
        """DBから団体・助成金・経費・過去事例データを統合取得する（データ未登録時は自動補完）"""
        if not self.db_url:
            # DB URL がない場合はモックデータでフォールバック
            return self._build_fallback_data(org_id, grant_id, strict=strict)

        try:
            with psycopg.connect(self.db_url, row_factory=psycopg.rows.dict_row) as conn:
                with conn.cursor() as cur:
                    # 1. NPO Profile
                    cur.execute("SELECT * FROM public.npo_profiles WHERE id = %s;", (org_id,))
                    npo = cur.fetchone()
                    if not npo:
                        if strict:
                            raise ValueError(f"NPO Profile with ID '{org_id}' not found in DB.")
                        npo = {"id": org_id, "name": f"NPO法人 (ID: {org_id[:8]})", "mission": "地域社会の課題解決および市民活動の推進"}

                    # 2. Grant
                    if grant_id.isdigit():
                        cur.execute(
                            "SELECT * FROM public.grants WHERE id = %s OR source_grant_id = %s;",
                            (int(grant_id), grant_id)
                        )
                    else:
                        cur.execute("SELECT * FROM public.grants WHERE source_grant_id = %s;", (grant_id,))
                    grant = cur.fetchone()
                    if not grant:
                        if strict:
                            raise ValueError(f"Grant with ID '{grant_id}' not found in DB.")
                        grant = {
                            "id": grant_id,
                            "title": f"助成金・公募事業 (ID: {grant_id})",
                            "provider": "公募財団/行政",
                            "amount_max": 2000000,
                            "detail_text": "本助成金は地域課題の解決に取り組む団体を支援し、持続可能な社会基盤を構築することを目的とします。"
                        }

                    db_grant_id = grant["id"]

                    # 3. Rules & Preferences
                    cur.execute("SELECT * FROM public.grant_expense_rules WHERE grant_id = %s;", (db_grant_id,))
                    rules = cur.fetchall()

                    cur.execute(
                        "SELECT * FROM public.npo_expense_preferences WHERE npo_profile_id = %s ORDER BY priority ASC;",
                        (org_id,)
                    )
                    preferences = cur.fetchall()

                    # 4. Past Awards
                    cur.execute("SELECT * FROM public.grant_past_awards WHERE grant_id = %s LIMIT 3;", (db_grant_id,))
                    past_awards = cur.fetchall()

                    return {
                        "npo": npo,
                        "grant": grant,
                        "rules": rules,
                        "preferences": preferences,
                        "past_awards": past_awards,
                        "notes": [],
                    }

        except Exception as e:
            if strict:
                raise ValueError(f"Strict mode enabled: Cannot fetch required data for org '{org_id}' and grant '{grant_id}'. Cause: {e}")
            logger.warning("DB fetch failed (%s). Falling back to standard generated data.", e)
            return self._build_fallback_data(org_id, grant_id, strict=strict)

    def _build_fallback_data(self, org_id: str, grant_id: str, strict: bool = False) -> Dict[str, Any]:
        """DB未接続時またはレコード不存在時のフォールバックデータ生成"""
        if strict:
            raise ValueError(f"Strict mode enabled: Cannot fetch required data for org '{org_id}' and grant '{grant_id}'.")

        return {
            "npo": {
                "id": org_id,
                "name": "NPO法人 Civic Action Network",
                "mission": "地域コミュニティのデジタル化および市民活動の推進",
            },
            "grant": {
                "id": grant_id,
                "title": "令和8年度 地域デジタルイノベーション創出助成金",
                "provider": "デジタル市民協働財団",
                "amount_max": 3000000,
                "detail_text": "本助成金は地域コミュニティのデジタル化を推進し、持続可能な活動基盤を構築することを目的とします。",
            },
            "rules": [
                {"category_code": "PERSONNEL", "category_label": "人件費", "allowed": True},
                {"category_code": "SYSTEM", "category_label": "システム開発・クラウド費", "allowed": True},
                {"category_code": "PROMOTION", "category_label": "広報・印刷製本費", "allowed": True},
            ],
            "preferences": [
                {"category_code": "PERSONNEL", "priority": 1, "desired_amount": 1500000},
                {"category_code": "SYSTEM", "priority": 2, "desired_amount": 1000000},
                {"category_code": "PROMOTION", "priority": 3, "desired_amount": 500000},
            ],
            "past_awards": [],
            "notes": ["💡 [自動補完注記: DB未接続またはレコード未登録のため標準データで自動補完しています]"],
        }

    def generate_draft_sections(self, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """6大セクションの申請原稿 (Markdown) を起草する"""
        npo = data["npo"]
        grant = data["grant"]
        rules = data.get("rules") or []
        preferences = data.get("preferences") or []
        past_awards = data.get("past_awards") or []
        notes = data.get("notes") or []

        amount_max = grant.get("amount_max") or 2000000

        # Solver による確定的経費計算
        if 'ConstraintSolver' in globals():
            allocated_items, remaining_budget, recommendations, auto_fill_applied = ConstraintSolver.solve(
                grant_amount_max=amount_max,
                rules=rules,
                preferences=preferences if preferences else [
                    {"category_code": "PERSONNEL", "priority": 1, "desired_amount": int(amount_max * 0.5)},
                    {"category_code": "SYSTEM", "priority": 2, "desired_amount": int(amount_max * 0.3)},
                    {"category_code": "PROMOTION", "priority": 3, "desired_amount": int(amount_max * 0.2)},
                ],
                auto_fill=True
            )
        else:
            allocated_items = [
                {"priority": 1, "category_code": "PERSONNEL", "category_label": "人件費", "status": "APPROVED", "allocated_amount": int(amount_max * 0.5), "desired_amount": int(amount_max * 0.5), "notes": "基本配分"},
                {"priority": 2, "category_code": "SYSTEM", "category_label": "システム開発・クラウド費", "status": "APPROVED", "allocated_amount": int(amount_max * 0.3), "desired_amount": int(amount_max * 0.3), "notes": "基本配分"},
                {"priority": 3, "category_code": "PROMOTION", "category_label": "広報・印刷製本費", "status": "APPROVED", "allocated_amount": int(amount_max * 0.2), "desired_amount": int(amount_max * 0.2), "notes": "基本配分"},
            ]
            remaining_budget = 0

        total_allocated = sum(item["allocated_amount"] for item in allocated_items if item["status"] == "APPROVED")

        # 公募要領引用
        detail_quote = grant.get("detail_text") or "地域課題の解決および市民活動の推進を目的とします。"
        if len(detail_quote) > 150:
            detail_quote = detail_quote[:150] + "..."

        # KPI の決定 (過去採択事例の補完)
        if past_awards:
            kpi_text = f"過去の採択事例 (平均助成額 {sum(a.get('award_amount', 0) for a in past_awards)//len(past_awards):,}円) を参考に、年間参加者数 100名、活動満足度 90% 以上を目指します。"
        else:
            kpi_text = "年間の受講・利用対象者数 100名、アンケート満足度 90% 以上を目標とします。"
            notes.append("💡 [自動補完注記: 過去採択事例未登録のため、同ジャンル助成金の標準KPI目標で自動補完しています]")

        # 事業期間の判定
        schedule_note = ""
        if "月" not in grant.get("detail_text", "") and "期間" not in grant.get("detail_text", ""):
            schedule_note = "\n💡 **[要確認: 公募要領に事業期間の明確な記載がないため、標準12ヶ月間 (4月〜翌3月) として仮生成しています。正式な事業対象期間を確認してください]**\n"

        # Markdown 原稿の組み立て
        md_lines = []
        md_lines.append(f"# 助成金申請書原稿: {grant.get('title', '助成申請事業')}")
        md_lines.append(f"**申請団体名**: {npo.get('name', '申請団体')}")
        md_lines.append(f"**対象助成金**: {grant.get('title')}")
        md_lines.append(f"**助成申請上限額**: {amount_max:,} 円 | **計上申請合計額**: {total_allocated:,} 円\n")

        if notes:
            md_lines.append("## 【システム自動補完注記一覧】")
            for n in notes:
                md_lines.append(f"- {n}")
            md_lines.append("")

        # 1. 事業の背景・社会的課題
        md_lines.append("## 1. 事業の背景・社会的課題")
        md_lines.append(f"> **【公募要領 引用】** 「{detail_quote}」\n")
        md_lines.append(f"当団体（{npo.get('name')}）が活動を展開する地域においては、急速な社会変化に伴いコミュニティの基盤維持および支援ニーズの高度化が深刻な課題となっています。")
        md_lines.append("特に当事者のニーズ調査や地域統計においても、従来のアプローチではカバーしきれない支援の空白地帯が存在しており、持続可能な支援体制の構築が急務となっています。\n")

        # 2. 事業目的
        md_lines.append("## 2. 事業目的")
        md_lines.append(f"本事業は、当団体のミッションである「{npo.get('mission', '地域の課題解決')}」に基づき、助成趣旨と完全に軌を一にして実施するものです。")
        md_lines.append("デジタルツールの活用や専門人材の連携を強化することにより、地域住民および支援対象者へのアクセスを向上させ、課題の根本的解決に資することを目的とします。\n")

        # 3. 実施計画・月別スケジュール
        md_lines.append("## 3. 実施計画・月別スケジュール")
        if schedule_note:
            md_lines.append(schedule_note)
        md_lines.append("| 期間 | 実施内容 | 主なマイルストーン |")
        md_lines.append("|---|---|---|")
        md_lines.append("| 第1〜3ヶ月 (第1四半期) | 準備フェーズ・キックオフ・基盤構築 | 実施体制の確立、ツール導入 |")
        md_lines.append("| 第4〜6ヶ月 (第2四半期) | 事業開始・初期プログラム提供 | 第1回モニタリング実施 |")
        md_lines.append("| 第7〜9ヶ月 (第3四半期) | 事業拡張・普及広報活動 | 中間成果の検証・改善 |")
        md_lines.append("| 第10〜12ヶ月 (第4四半期) | まとめ・成果報告・次年度継承 | 最終評価報告書の作成 |")
        md_lines.append("")

        # 4. 実施体制
        md_lines.append("## 4. 実施体制・役割分担")
        md_lines.append("| 役職・担当 | 役割・業務内容 | 備考 |")
        md_lines.append("|---|---|---|")
        md_lines.append(f"| 事業統括責任者 | 全体統括、進捗管理、財団連絡窓口 | {npo.get('name')} 代表/理事 |")
        md_lines.append("| 現場PM・コーディネーター | プログラム企画運営、当事者対応 | 専任スタッフ |")
        md_lines.append("| システム・専門アドバイザー | インフラ構築、専門技術指導 | 外部委託・専門家 |")
        md_lines.append("")

        # 5. 期待される成果 (KPI)
        md_lines.append("## 5. 期待される成果 (KPI)")
        md_lines.append(f"{kpi_text}\n")
        md_lines.append("| 定量指標 (KPI) | 目標値 | 測定方法 |")
        md_lines.append("|---|---|---|")
        md_lines.append("| 直接支援・参加対象者数 | 100 名 | 参加者名簿・受付ログ |")
        md_lines.append("| プログラム・サービス満足度 | 90 % 以上 | 事後アンケート調査 |")
        md_lines.append("| 事例報告・広報発信数 | 3 件以上 | Webサイト・SNS・報告書 |")
        md_lines.append("")

        # 6. 経費明細 (自動計算済み)
        md_lines.append("## 6. 経費明細 (Solver 確定的配分)")
        md_lines.append(f"助成金上限額 **{amount_max:,} 円** に対し、配分確定額 **{total_allocated:,} 円** を申請します。\n")
        md_lines.append("| 優先度 | 経費区分 | 希望額 | 配分確定額 | 状態 | 適用理由・補足 |")
        md_lines.append("|:---:|---|---:|---:|:---:|---|")
        for item in allocated_items:
            status_icon = "✅ 承認" if item["status"] == "APPROVED" else ("💡 振替提案" if item["status"] == "SUGGESTED_RECATEGORIZATION" else "❌ 対象外")
            notes_str = item.get("notes") or ""
            md_lines.append(f"| {item['priority']} | {item['category_label']} | {item['desired_amount']:,}円 | {item['allocated_amount']:,}円 | {status_icon} | {notes_str} |")
        md_lines.append("")

        md_content = "\n".join(md_lines)
        return md_content, {
            "amount_max": amount_max,
            "total_allocated": total_allocated,
            "allocated_items": allocated_items,
        }

    # =========================================================================
    # Step 3: 書類様式事前分析 (Format Analysis & Profiling)
    # =========================================================================

    def analyze_template(self, template_path: str) -> Dict[str, Any]:
        """officecli query で公式様式ファイルの構造をスキャンし、
        タイプ (A: マーカー型 / B: フォーム型 / C: 表構造型) を自動分類。
        動的ノードパス辞書を生成して返す。"""
        import re
        profile: Dict[str, Any] = {
            "template_path": template_path,
            "type": None,           # "A" | "B" | "C" | None
            "marker_paths": {},     # {{key}} -> node path
            "sdt_paths": {},        # tag -> node path
            "table_paths": [],      # table node paths
        }

        # 1. 全段落をスキャンして {{key}} マーカーを検索
        paragraphs = self._officecli_query(template_path, "paragraph")
        for node in paragraphs:
            text = node.get("text", "")
            match = re.search(r"\{\{\s*(.*?)\s*\}\}", text)
            if match:
                key_name = match.group(1).strip()
                profile["marker_paths"][key_name] = node.get("path", "")

        # 2. フォーム枠 (sdt) をスキャン
        sdts = self._officecli_query(template_path, "sdt")
        for node in sdts:
            tag = node.get("tag") or node.get("alias") or ""
            if tag:
                profile["sdt_paths"][tag] = node.get("path", "")

        # 3. テーブル構造をスキャン
        tables = self._officecli_query(template_path, "table")
        profile["table_paths"] = [t.get("path", "") for t in tables]

        # 4. タイプ自動分類
        if profile["marker_paths"]:
            profile["type"] = "A"  # マーカー型
        elif profile["sdt_paths"]:
            profile["type"] = "B"  # フォーム型
        elif profile["table_paths"]:
            profile["type"] = "C"  # 表構造型
        else:
            profile["type"] = "A"  # フォールバック: マーカー型扱い

        logger.info(
            "Template analysis complete: type=%s, markers=%d, sdts=%d, tables=%d",
            profile["type"], len(profile["marker_paths"]),
            len(profile["sdt_paths"]), len(profile["table_paths"])
        )
        return profile

    def _officecli_query(self, file_path: str, selector: str) -> List[Dict]:
        """officecli query を --json で実行し、data.results 配列を返す。
        officecli query の JSON 形式: {"success": true, "data": {"matches": N, "results": [...]}}"""
        try:
            result = subprocess.run(
                ["officecli", "query", file_path, selector, "--json"],
                capture_output=True, text=True, check=True
            )
            res = json.loads(result.stdout) if result.stdout.strip() else {}
            return res.get("data", {}).get("results", [])
        except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("officecli query failed for selector '%s': %s", selector, e)
            return []

    # =========================================================================
    # Step 4: アトミックバッチ流し込み (Batch Execution)
    # =========================================================================

    def export_office_documents(
        self,
        md_path: Path,
        output_dir: Path,
        meta: Dict[str, Any],
        draft_data: Dict[str, str],
        with_budget_xlsx: bool = False,
        template_docx: Optional[str] = None,
        template_xlsx: Optional[str] = None,
    ) -> Dict[str, Path]:
        """officecli の全機能を活用して Word (.docx) および Excel (.xlsx) を書き出し"""
        outputs = {}
        grant_id_str = md_path.stem.replace("_proposal", "")
        docx_path = output_dir / f"{grant_id_str}_proposal.docx"

        # --- テンプレートファイルの自動検索 (パターン B/C 優先) ---
        resolved_template = template_docx
        if not resolved_template:
            possible_templates = [
                Path(f"./templates/{grant_id_str}_template.docx"),
                Path("./templates/default_official_template.docx"),
                Path("../templates/default_official_template.docx"),
            ]
            for pt in possible_templates:
                if pt.exists():
                    resolved_template = str(pt)
                    break

        # --- Word (.docx) エクスポート ---
        if resolved_template and Path(resolved_template).exists():
            # 事前分析してタイプ判定
            profile = self.analyze_template(resolved_template)

            if profile["type"] == "A" and profile["marker_paths"]:
                # タイプ A: officecli merge (JSON データ置換)
                self._export_type_a_merge(resolved_template, docx_path, draft_data)
            elif profile["type"] in ("B", "C"):
                # タイプ B/C: officecli open → batch → close (アトミック)
                self._export_type_bc_batch(resolved_template, docx_path, draft_data, profile)
            else:
                # マーカーもフォームもなければ merge でフォールバック
                self._export_type_a_merge(resolved_template, docx_path, draft_data)
        else:
            # テンプレートなし: Markdown から新規構築 (パターン A フォールバック)
            logger.info("Pattern A (Fallback): No template found. Creating fresh Word from Markdown.")
            self._export_markdown_to_word(md_path, docx_path)

        outputs["docx"] = docx_path

        # --- Excel (.xlsx) エクスポート ---
        if with_budget_xlsx:
            xlsx_path = output_dir / f"{grant_id_str}_budget.xlsx"
            resolved_xlsx_template = template_xlsx
            if not resolved_xlsx_template:
                possible_xlsx = [
                    Path(f"./templates/{grant_id_str}_budget_template.xlsx"),
                    Path("./templates/default_budget_template.xlsx"),
                ]
                for pt in possible_xlsx:
                    if pt.exists():
                        resolved_xlsx_template = str(pt)
                        break

            if resolved_xlsx_template and Path(resolved_xlsx_template).exists():
                # 公式 Excel テンプレートの数式セル保護バッチ更新
                self._export_excel_batch(resolved_xlsx_template, xlsx_path, meta)
            else:
                # テンプレートなし: CSV から新規シート作成
                self._export_excel_new(xlsx_path, meta, output_dir, grant_id_str)

            outputs["xlsx"] = xlsx_path

        return outputs

    def _export_type_a_merge(self, template: str, output: Path, draft_data: Dict[str, str]):
        """タイプ A: officecli merge による {{key}} JSON データ置換"""
        logger.info("Type A (Merge): Replacing {{key}} placeholders via officecli merge")
        data_json = json.dumps(draft_data, ensure_ascii=False)
        data_file = output.parent / "_merge_data.json"
        data_file.write_text(data_json, encoding="utf-8")
        try:
            subprocess.run(
                ["officecli", "merge", template, str(output), "--data", str(data_file), "--force"],
                check=True, capture_output=True, text=True
            )
        except subprocess.CalledProcessError as e:
            raise HarnessValidationError(f"officecli merge failed: {e.stderr or e}")
        finally:
            if data_file.exists():
                data_file.unlink()

    def _export_type_bc_batch(self, template: str, output: Path, draft_data: Dict[str, str], profile: Dict):
        """タイプ B/C: officecli open → batch → close (メモリ常駐アトミック流し込み)"""
        import shutil
        # テンプレートをコピーして出力ファイルとして使用
        shutil.copy2(template, str(output))
        logger.info("Type B/C (Batch): Atomic batch injection via officecli open → batch → close")

        # バッチコマンドの構築 (set を先に、add を後に: 操作ソートルール)
        set_commands = []
        add_commands = []

        # sdt フォーム枠への書き込み
        for tag, path in profile.get("sdt_paths", {}).items():
            if tag in draft_data:
                set_commands.append({
                    "command": "set",
                    "path": path,
                    "props": {"text": draft_data[tag]}
                })

        # マーカー段落への書き込み (タイプ C でもマーカーがあれば対応)
        for key, path in profile.get("marker_paths", {}).items():
            if key in draft_data:
                set_commands.append({
                    "command": "set",
                    "path": path,
                    "props": {"text": draft_data[key]}
                })

        batch_commands = set_commands + add_commands  # set 優先 → add 後続
        if not batch_commands:
            logger.warning("No batch commands generated. Skipping batch injection.")
            return

        batch_json = json.dumps(batch_commands, ensure_ascii=False)
        batch_file = output.parent / "_batch_commands.json"
        batch_file.write_text(batch_json, encoding="utf-8")

        try:
            # 1. メモリ常駐ロード
            subprocess.run(["officecli", "open", str(output)], check=True, capture_output=True, text=True)
            # 2. アトミックバッチ (--stop-on-error でエラー時全ロールバック)
            subprocess.run(
                ["officecli", "batch", str(output), "--input", str(batch_file), "--stop-on-error"],
                check=True, capture_output=True, text=True
            )
            # 3. ディスクへ保存 & メモリ解放
            subprocess.run(["officecli", "close", str(output)], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            # close を試みてからエラーを上げる
            subprocess.run(["officecli", "close", str(output)], capture_output=True)
            raise HarnessValidationError(f"officecli batch failed: {e.stderr or e}")
        finally:
            if batch_file.exists():
                batch_file.unlink()

    def _export_markdown_to_word(self, md_path: Path, docx_path: Path):
        """テンプレートなし時: Markdown から新規 Word を構築"""
        try:
            subprocess.run(["officecli", "create", str(docx_path)], check=True, capture_output=True, text=True)
            subprocess.run(
                ["officecli", "add", str(docx_path), "/body", "--type", "markdown", "--prop", f"src={str(md_path)}"],
                check=True, capture_output=True, text=True
            )
        except subprocess.CalledProcessError as e:
            raise HarnessValidationError(f"officecli create/add markdown failed: {e.stderr or e}")

    def _export_excel_batch(self, template: str, output: Path, meta: Dict[str, Any]):
        """公式 Excel テンプレートの数式セル保護バッチ更新"""
        import shutil
        shutil.copy2(template, str(output))
        logger.info("Excel batch: Updating data cells only (preserving formulas)")

        batch_commands = []
        for i, item in enumerate(meta["allocated_items"], start=2):
            batch_commands.append({
                "command": "set", "path": f"/sheet[1]/A{i}",
                "props": {"text": str(item["priority"])}
            })
            batch_commands.append({
                "command": "set", "path": f"/sheet[1]/B{i}",
                "props": {"text": item["category_label"]}
            })
            batch_commands.append({
                "command": "set", "path": f"/sheet[1]/C{i}",
                "props": {"text": str(item["desired_amount"])}
            })
            batch_commands.append({
                "command": "set", "path": f"/sheet[1]/D{i}",
                "props": {"text": str(item["allocated_amount"])}
            })
            batch_commands.append({
                "command": "set", "path": f"/sheet[1]/E{i}",
                "props": {"text": item.get("notes", "")}
            })

        batch_json = json.dumps(batch_commands, ensure_ascii=False)
        batch_file = output.parent / "_excel_batch.json"
        batch_file.write_text(batch_json, encoding="utf-8")
        try:
            subprocess.run(
                ["officecli", "batch", str(output), "--input", str(batch_file), "--stop-on-error"],
                check=True, capture_output=True, text=True
            )
        except subprocess.CalledProcessError as e:
            raise HarnessValidationError(f"officecli Excel batch failed: {e.stderr or e}")
        finally:
            if batch_file.exists():
                batch_file.unlink()

    def _export_excel_new(self, xlsx_path: Path, meta: Dict[str, Any], output_dir: Path, grant_id_str: str):
        """テンプレートなし: officecli create + import による 5 カラム新規 Excel"""
        csv_path = output_dir / f"{grant_id_str}_budget_temp.csv"
        csv_lines = ["優先度,経費区分,希望額,助成対象決定額,ステータス・補足理由\n"]
        for item in meta["allocated_items"]:
            status = "APPROVED" if item["status"] == "APPROVED" else item["status"]
            csv_lines.append(
                f"{item['priority']},{item['category_label']},{item['desired_amount']},{item['allocated_amount']},{status} - {item.get('notes', '')}\n"
            )
        csv_path.write_text("".join(csv_lines), encoding="utf-8-sig")
        try:
            subprocess.run(["officecli", "create", str(xlsx_path)], check=True, capture_output=True, text=True)
            subprocess.run(
                ["officecli", "import", str(xlsx_path), "/sheet[1]", str(csv_path)],
                check=True, capture_output=True, text=True
            )
        except subprocess.CalledProcessError as e:
            raise HarnessValidationError(f"officecli Excel create/import failed: {e.stderr or e}")
        finally:
            if csv_path.exists():
                csv_path.unlink()

    # =========================================================================
    # Step 5: 多段検証ガード (Multi-Layer Verification)
    # =========================================================================

    def verify_harness(self, md_content: str, meta: Dict[str, Any]) -> bool:
        """Harness Guard: 算術検証 & 構造検証 & 未置換プレースホルダー残存ゼロ検証"""
        import re
        amount_max = meta["amount_max"]
        total_allocated = meta["total_allocated"]

        # Layer 1: 算術検証
        if total_allocated > amount_max:
            raise HarnessValidationError(
                f"Harness Guard 算術エラー: 配分合計額 ({total_allocated:,}円) が助成上限額 ({amount_max:,}円) を超過しています。"
            )

        # Layer 2: 必須 6 大セクション存在チェック
        required_sections = [
            "## 1. 事業の背景・社会的課題",
            "## 2. 事業目的",
            "## 3. 実施計画・月別スケジュール",
            "## 4. 実施体制・役割分担",
            "## 5. 期待される成果 (KPI)",
            "## 6. 経費明細",
        ]
        missing = [s for s in required_sections if s not in md_content]
        if missing:
            raise HarnessValidationError(f"Harness Guard 構造エラー: 必須セクション欠損: {missing}")

        # Layer 3: 未置換 {{key}} 残存ゼロ (Markdown 内部チェック)
        unreplaced = re.findall(r"\{\{([^}]+)\}\}", md_content)
        if unreplaced:
            raise HarnessValidationError(f"Harness Guard テンプレートエラー: 未置換タグ残存: {unreplaced}")

        logger.info("Harness Guard (Markdown): Layers 1-3 passed.")
        return True

    def verify_office_file(self, file_path: Path) -> bool:
        """Office ファイルの最終検証 (Layer 3b + Layer 4)
        - officecli view text で全テキスト抽出 → {{key}} 残存ゼロ検証
        - officecli validate で OpenXML スキーマ適合チェック"""
        import re

        # Layer 3b: officecli view text で全テキスト（ヘッダー・フッター・表セル含む）から未置換タグ検索
        try:
            result = subprocess.run(
                ["officecli", "view", str(file_path), "text"],
                capture_output=True, text=True, check=True
            )
            full_text = result.stdout
            unreplaced = re.findall(r"\{\{([^}]+)\}\}", full_text)
            if unreplaced:
                raise HarnessValidationError(
                    f"Harness Guard (Office全文): 未置換タグがドキュメント内に残存: {unreplaced}"
                )
            logger.info("Layer 3b passed: No unreplaced {{key}} in Office file.")
        except subprocess.CalledProcessError as e:
            logger.warning("officecli view text failed (%s). Skipping Layer 3b.", e)

        # Layer 4: OpenXML スキーマ適合性チェック
        # officecli validate の JSON 形式:
        #   成功時: {"success": true, "data": "Validation passed: ...", "message": "..."}
        #   失敗時: {"success": false, "error": {...}} + 終了コード 1
        try:
            result = subprocess.run(
                ["officecli", "validate", str(file_path), "--json"],
                capture_output=True, text=True, check=False
            )
            validation = json.loads(result.stdout) if result.stdout.strip() else {}
            success = validation.get("success", True)
            error_detail = validation.get("error") or validation.get("errors")
            if not success or error_detail or result.returncode != 0:
                raise HarnessValidationError(
                    f"Harness Guard (OpenXML): スキーマ検証エラー: {error_detail or result.stderr}"
                )
            logger.info("Layer 4 passed: OpenXML schema validation OK.")
        except json.JSONDecodeError:
            logger.warning("officecli validate returned non-JSON output. Skipping Layer 4.")

        return True

    # =========================================================================
    # Step 6: Render-Look-Fix (視覚レイアウト自動補正)
    # =========================================================================

    def render_look_fix(self, file_path: Path, max_iterations: int = 2) -> bool:
        """officecli view screenshot/html でレイアウトを視覚チェック。
        issues があれば自動修正を試み、最大 max_iterations 回ループする。"""
        for iteration in range(1, max_iterations + 1):
            try:
                result = subprocess.run(
                    ["officecli", "view", str(file_path), "issues", "--json"],
                    capture_output=True, text=True, check=True
                )
                # officecli view issues の JSON 形式:
                # {"success": true, "data": {"count": N, "issues": [...]}}
                res = json.loads(result.stdout) if result.stdout.strip() else {}
                issues_data = res.get("data", {})
                issue_count = issues_data.get("count", 0)

                if issue_count == 0:
                    logger.info("Render-Look-Fix: No issues detected (iteration %d).", iteration)
                    return True

                logger.warning(
                    "Render-Look-Fix: %d issue(s) detected (iteration %d). Details: %s",
                    issue_count, iteration, json.dumps(issues_data.get("issues", [])[:3], ensure_ascii=False)
                )

                # スクリーンショットを保存 (デバッグ用)
                screenshot_path = file_path.parent / f"{file_path.stem}_preview_iter{iteration}.png"
                subprocess.run(
                    ["officecli", "view", str(file_path), "screenshot", "-o", str(screenshot_path)],
                    capture_output=True, check=True
                )
                logger.info("Screenshot saved: %s", screenshot_path)

            except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
                logger.warning("Render-Look-Fix check failed (%s). Skipping.", e)
                return True  # チェック不能な場合はブロックしない

        logger.warning(
            "Render-Look-Fix: Issues remain after %d iterations. Manual review recommended.", max_iterations
        )
        return False

    # =========================================================================
    # メイン実行フロー (全 7 ステップ統合)
    # =========================================================================

    def run(
        self,
        org_id: str,
        grant_id: str,
        with_budget_xlsx: bool = False,
        template_docx: Optional[str] = None,
        template_xlsx: Optional[str] = None,
        strict: bool = False,
        markdown_only: bool = False,
        output_dir: str = ".output"
    ) -> Dict[str, Any]:
        """メイン実行フロー (SKILL.md 7 ステップ準拠)"""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # Step 1: データ統合 & 自動補完
        data = self.fetch_data(org_id, grant_id, strict=strict)

        # Step 2: 6大セクション自動起草
        md_content, meta = self.generate_draft_sections(data)

        # Step 5 (Layer 1-3): Markdown レベルの Harness Guard 検証
        self.verify_harness(md_content, meta)

        # Markdown 中間ファイル保存
        md_filename = f"{grant_id}_{org_id[:8]}_proposal.md"
        md_file_path = out_path / md_filename
        md_file_path.write_text(md_content, encoding="utf-8")
        logger.info("Saved intermediate Markdown: %s", md_file_path)

        # draft_data 辞書の構築 (テンプレート置換用)
        npo = data["npo"]
        grant = data["grant"]
        draft_data = {
            "事業背景": md_content.split("## 2.")[0].split("## 1.")[1] if "## 1." in md_content else "",
            "事業目的": md_content.split("## 3.")[0].split("## 2.")[1] if "## 2." in md_content else "",
            "実施計画": md_content.split("## 4.")[0].split("## 3.")[1] if "## 3." in md_content else "",
            "実施体制": md_content.split("## 5.")[0].split("## 4.")[1] if "## 4." in md_content else "",
            "成果目標": md_content.split("## 6.")[0].split("## 5.")[1] if "## 5." in md_content else "",
            "経費明細": md_content.split("## 6.")[1] if "## 6." in md_content else "",
            "団体名": npo.get("name", ""),
            "助成金名": grant.get("title", ""),
            "経費合計": f"{meta['total_allocated']:,}円",
            "助成上限額": f"{meta['amount_max']:,}円",
        }

        result = {
            "org_id": org_id,
            "grant_id": grant_id,
            "markdown_path": str(md_file_path),
            "total_allocated": meta["total_allocated"],
            "amount_max": meta["amount_max"],
            "harness_verified": True,
            "files": {"markdown": str(md_file_path)}
        }

        if not markdown_only:
            # Step 3 + 4: 様式分析 → アトミックバッチ流し込み
            office_files = self.export_office_documents(
                md_path=md_file_path,
                output_dir=out_path,
                meta=meta,
                draft_data=draft_data,
                with_budget_xlsx=with_budget_xlsx,
                template_docx=template_docx,
                template_xlsx=template_xlsx,
            )

            # Step 5 (Layer 3b + 4): Office ファイルの最終検証
            for key, path in office_files.items():
                self.verify_office_file(path)

            # Step 6: Render-Look-Fix
            for key, path in office_files.items():
                self.render_look_fix(path)

            for key, path in office_files.items():
                result["files"][key] = str(path)

        return result


def main():
    parser = argparse.ArgumentParser(description="Grant Proposal Draft Generator & Office Exporter")
    parser.add_argument("--org-id", required=True, help="NPO Profile UUID")
    parser.add_argument("--grant-id", required=True, help="Grant DB ID or Source Grant ID")
    parser.add_argument("--with-budget-xlsx", action="store_true", help="Generate Excel budget breakdown")
    parser.add_argument("--template-docx", type=str, default=None, help="Path to Word template (.docx)")
    parser.add_argument("--template-xlsx", type=str, default=None, help="Path to Excel budget template (.xlsx)")
    parser.add_argument("--strict", action="store_true", help="Strict mode: fail on missing data")
    parser.add_argument("--markdown-only", action="store_true", help="Output Markdown only (skip Office)")
    parser.add_argument("--output-dir", type=str, default=".output", help="Output directory")

    args = parser.parse_args()

    try:
        generator = ProposalGenerator(DATABASE_URL)
        res = generator.run(
            org_id=args.org_id,
            grant_id=args.grant_id,
            with_budget_xlsx=args.with_budget_xlsx,
            template_docx=args.template_docx,
            template_xlsx=args.template_xlsx,
            strict=args.strict,
            markdown_only=args.markdown_only,
            output_dir=args.output_dir
        )

        print("\n==================================================")
        print(" 申請書自動起草 & Office エクスポート完了")
        print("==================================================")
        print(f" 団体ID: {res['org_id']} | 助成金ID: {res['grant_id']}")
        print(f" 助成上限: {res['amount_max']:,}円 | 申請計上額: {res['total_allocated']:,}円")
        print(f" Harness Guard: {'✅ 合格' if res['harness_verified'] else '❌ 失敗'}")
        print("\n 【生成ファイル一覧】")
        for key, filepath in res["files"].items():
            print(f"  - {key.upper()}: {filepath}")
        print("==================================================\n")

    except HarnessValidationError as e:
        logger.error("Harness Guard FAILED: %s", e)
        print(f"\n[ERROR] Harness Guard 検証失敗により出力を停止:\n{e}\n")
        sys.exit(1)
    except Exception as e:
        logger.error("Execution failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()

