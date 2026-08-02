-- Phase 2: 6-Gate リファクタリング用 alerts テーブル拡張
-- 適用日: 2026-08-03

ALTER TABLE public.alerts
  ADD COLUMN IF NOT EXISTS overall_status TEXT DEFAULT 'INELIGIBLE',
  ADD COLUMN IF NOT EXISTS failed_gate_codes TEXT[] DEFAULT '{}'::TEXT[];
