-- Phase 2: 振替キーワードの助成金単位カスタマイズ対応
-- grant_expense_rules に recategory_keywords JSONB カラムを追加する。
-- NULL: constants.py のデフォルト KEYWORD_RECATEGORY_MAP をフォールバック
-- []: 明示的にこのカテゴリへの振替を無効化
-- ["kw1", "kw2", ...]: カスタムキーワードで上書き

ALTER TABLE public.grant_expense_rules
  ADD COLUMN IF NOT EXISTS recategory_keywords JSONB DEFAULT NULL;

COMMENT ON COLUMN public.grant_expense_rules.recategory_keywords IS
  '振替検知キーワードリスト (JSON文字列配列)。NULLでconstants.pyデフォルト、空配列で振替無効';
