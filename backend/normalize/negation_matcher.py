"""
NegationMatcher モジュール (negation_matcher.py)

「NPO法人以外は対象外」「常駐なし」等の否定表現を含むテキストに対して、
キーワード周辺のコンテキストを解析し、誤判定を防止する Negation-Aware Matching。
"""

import re
from typing import List

NEGATION_WORDS = ["なし", "ない", "不要", "除く", "ません", "ありません", "なく", "対象外", "禁止"]

def has_matching_keyword_without_negation(
    text: str, keywords: List[str], negation_window: int = 12
) -> bool:
    """
    指定キーワードの前後 N 文字以内に否定語が存在する場合は False を返す。
    例: "NPO法人以外は対象外" -> False
        "NPO法人が対象" -> True
    """
    if not text or not keywords:
        return False

    for kw in keywords:
        for m in re.finditer(re.escape(kw), text):
            start = max(0, m.start() - negation_window)
            end = min(len(text), m.end() + negation_window)
            context = text[start:end]
            if not any(neg in context for neg in NEGATION_WORDS):
                return True
    return False
