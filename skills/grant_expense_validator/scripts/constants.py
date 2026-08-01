"""
Grant Expense Validator — 共有定数モジュール

経費区分ラベルと振替キーワードマッピングを一元管理する。
テスト側からも同じ定数を import して整合性を担保する。

TODO(Phase-2): 助成金ごとの個別キーワード対応が必要になった場合、
  grant_expense_rules テーブルに recategory_keywords JSONB カラムを追加し、
  DB 値が NULL の場合にここのデフォルト値をフォールバックとして使用する。
  See: docs/TODO.md
"""

# 経費区分コード → 日本語ラベル
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

# 振替先カテゴリ → 検知キーワードリスト
# notes テキスト内にこれらのキーワードが含まれる場合、
# allowed=False の区分から該当カテゴリへの振替を提案する。
KEYWORD_RECATEGORY_MAP = {
    "SYSTEM": [
        "API", "LLM", "OPENAI", "CLAUDE", "GEMINI",
        "SUPABASE", "NEON", "DB", "DATABASE", "データベース",
        "クラウド", "サーバー", "インフラ", "ホスティング",
        "MODAL", "VERCEL", "AWS", "GCP", "SAAS", "GPU", "開発"
    ],
    "PROMOTION": [
        "チラシ", "印刷", "広告", "パンフレット", "ポスター", "WEB広告", "動画", "PR"
    ],
    "OUTSOURCING": [
        "講師", "謝礼", "委託", "コンサル", "デザイン依頼", "外部開発", "エンジニア"
    ],
}
