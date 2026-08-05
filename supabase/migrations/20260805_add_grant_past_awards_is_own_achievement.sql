-- =============================================================================
-- Migration: grant_past_awards に自己採択フラグ列 is_own_achievement を追加
-- Date: 2026-08-05
-- 背景: analyze_past_awards.py(INSERT列) と ingest_npo_profile.py(SELECT WHERE)
--   の両方が is_own_achievement 列を前提に実装されているが、初期 DDL に列が
--   存在しなかったため追加漏れ。この列が無いと両スクリプトが実行時 SQL
--   エラー(静かに握りつぶされる)で機能しない。
-- 冪等性: IF NOT EXISTS により再適用安全。
-- =============================================================================

ALTER TABLE public.grant_past_awards
  ADD COLUMN IF NOT EXISTS is_own_achievement BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN public.grant_past_awards.is_own_achievement IS
  '自団体の採択実績かどうか(TRUE=自己採択で NPO embedding に利用)';
