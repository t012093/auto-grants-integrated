"""
CSSExtractor モジュール (css_extractor.py)

HTML から CSS セレクターを用いて確定的 (ハルシネーション0%) に要素を抽出し、
必須項目が欠落しているデータは推測補完せず DropRecord ガードレールとして記録する。
"""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import logging
import re
from typing import List, Dict, Any, Tuple, Optional
from bs4 import BeautifulSoup
import markdownify

logger = logging.getLogger(__name__)

@dataclass
class DropRecord:
    """抽出失敗時の記録。AI 推測補完を防止し、不整合を透明化する。"""
    source_id: str
    reason: str
    raw_snippet: str
    dropped_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

@dataclass
class ExtractedGrant:
    source_id: str
    title: str
    provider: str
    url: str
    deadline: Optional[str] = None
    description_md: Optional[str] = None
    is_deterministic: bool = True

class CSSExtractor:
    """
    DOMProfile 定義に基づいて確定的抽出を実行するクラス
    """
    NOISE_PATTERNS = [
        r".*応募する.*",
        r".*申し込む.*",
        r".*ログイン.*",
        r".*保存.*",
        r".*シェア.*",
        r".*印刷.*",
        r"^(Apply|Save|Share|Print|Sign in)$",
        r"^\s*$",
    ]

    def extract_list(self, html: str, profile: Dict[str, Any]) -> Tuple[List[ExtractedGrant], List[DropRecord]]:
        soup = BeautifulSoup(html, "lxml")
        grants: List[ExtractedGrant] = []
        drops: List[DropRecord] = []

        selectors = profile.get("selectors", {})
        list_sel = selectors.get("list_selector")
        if not list_sel:
            return grants, drops

        items = soup.select(list_sel)
        source_id = profile.get("source_id", "unknown")
        provider_default = selectors.get("provider_default", "")

        for item in items:
            title_el = item.select_one(selectors.get("title_selector", "")) if selectors.get("title_selector") else None
            url_el = item.select_one(selectors.get("url_selector", "")) if selectors.get("url_selector") else None
            deadline_el = item.select_one(selectors.get("deadline_selector", "")) if selectors.get("deadline_selector") else None
            provider_el = item.select_one(selectors.get("provider_selector", "")) if selectors.get("provider_selector") else None

            title = title_el.get_text(strip=True) if title_el else ""
            url = url_el.get("href", "") if url_el else ""
            deadline = deadline_el.get_text(strip=True) if deadline_el else None
            provider = provider_el.get_text(strip=True) if provider_el else provider_default

            # ガードレール: 必須要素（title / url / provider）のいずれかが欠落している場合はドロップ
            if not title or not url or not provider:
                reason = "missing_title" if not title else ("missing_url" if not url else "missing_provider")
                drops.append(DropRecord(
                    source_id=source_id,
                    reason=reason,
                    raw_snippet=str(item)[:200]
                ))
                continue  # 推測補完せずドロップ

            grants.append(ExtractedGrant(
                source_id=source_id,
                title=title,
                provider=provider,
                url=url,
                deadline=deadline,
                is_deterministic=True
            ))

        return grants, drops

    def html_to_markdown_clean(self, html: str, body_selector: Optional[str] = None) -> str:
        """
        DOM AST から確定的に Markdown を生成し、UI ノイズ行を除去する
        """
        soup = BeautifulSoup(html, "lxml")
        
        # 不要タグの除去
        for tag in soup.select("nav, header, footer, aside, script, style, .ad, .sidebar"):
            tag.decompose()

        target = soup.select_one(body_selector) if body_selector else None
        if not target:
            target = soup.body or soup

        md = markdownify.markdownify(
            str(target),
            heading_style="ATX",
            bullets="-",
            strip=["img"]
        )

        # ノイズ行の除去
        lines = md.split("\n")
        filtered_lines = [
            line for line in lines
            if not any(re.match(p, line.strip(), re.IGNORECASE) for p in self.NOISE_PATTERNS)
        ]
        
        clean_md = "\n".join(filtered_lines)
        return re.sub(r"\n{3,}", "\n\n", clean_md).strip()
