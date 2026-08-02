-- Phase 2: Gate 5 特定要件 RAG 検索の前提カラム追加
-- 適用日: 2026-08-02

-- 1. grants テーブルに要件文リストカラムを追加
ALTER TABLE public.grants
  ADD COLUMN IF NOT EXISTS requirement_sentences TEXT[] DEFAULT '{}'::TEXT[];

-- 2. alerts テーブルに詳細構造化レポート JSON カラムを追加
ALTER TABLE public.alerts
  ADD COLUMN IF NOT EXISTS report_json JSONB DEFAULT '{}'::JSONB;
