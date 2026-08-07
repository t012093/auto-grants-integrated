-- =============================================================================
-- Migration: 8軸採択予測 (grant_lifecycle_manager predict_win_rate) 用テーブル
-- Target: Supabase (PostgreSQL 15 + pgvector)
-- Date: 2026-08-06
-- 対応仕様: skills/grant_lifecycle_manager/spec.md §8
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. grant_win_rank (8軸採択予測の結果・Upsert)
--    model_version: 使用した重み定義の版 (win_rate_weights.json の version)。
--      重み更新で陳腐化検知に使う (spec §9.3)。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.grant_win_rank (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  npo_profile_id uuid NOT NULL REFERENCES public.npo_profiles(id) ON DELETE CASCADE,
  grant_id       int  NOT NULL REFERENCES public.grants(id) ON DELETE CASCADE,
  overall_score  int,          -- 0-100 (評価済み軸のみの重み付き平均)
  coverage       real,         -- 評価済み軸重み (0-1)
  rank           text,         -- A/B/C/D
  provisional    boolean DEFAULT false,  -- 過去採択データ不足等で暫定
  model_version  text,         -- win_rate_weights.json の version (陳腐化検知)
  axes_json      jsonb,        -- 8軸スコア・evaluated・source
  improvement_notes jsonb,     -- 弱点改善注記
  created_at     timestamptz DEFAULT NOW(),
  updated_at     timestamptz DEFAULT NOW(),
  UNIQUE (npo_profile_id, grant_id)
);

CREATE INDEX IF NOT EXISTS idx_grant_win_rank_npo ON public.grant_win_rank(npo_profile_id);
CREATE INDEX IF NOT EXISTS idx_grant_win_rank_grant ON public.grant_win_rank(grant_id);

CREATE TRIGGER set_grant_win_rank_updated_at
BEFORE UPDATE ON public.grant_win_rank
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- ---------------------------------------------------------------------------
-- 2. grant_applications (採択フィードバック・キャリブレーション用, spec §8.2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.grant_applications (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  npo_profile_id uuid NOT NULL REFERENCES public.npo_profiles(id) ON DELETE CASCADE,
  grant_id       int  NOT NULL REFERENCES public.grants(id) ON DELETE CASCADE,
  appeared_at    date,          -- 申請提出日
  result         text,          -- AWARDED / REJECTED / PENDING
  reject_reason  text,          -- 不採択理由（任意）
  created_at     timestamptz DEFAULT NOW(),
  UNIQUE (npo_profile_id, grant_id)  -- 採択結果の冪等 Upsert 用 (record_application_result)
);

CREATE INDEX IF NOT EXISTS idx_grant_applications_npo ON public.grant_applications(npo_profile_id);
CREATE INDEX IF NOT EXISTS idx_grant_applications_grant ON public.grant_applications(grant_id);

-- ---------------------------------------------------------------------------
-- 3. RLS (Row Level Security)
--    自団体の rank / 応募結果のみ読み書き可能 (service_role は全権限)
-- ---------------------------------------------------------------------------
ALTER TABLE public.grant_win_rank ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.grant_applications ENABLE ROW LEVEL SECURITY;

CREATE POLICY grant_win_rank_own_select ON public.grant_win_rank
  FOR SELECT USING (npo_profile_id IN (SELECT id FROM public.npo_profiles WHERE owner_user_id = auth.uid()));
CREATE POLICY grant_win_rank_own_insert ON public.grant_win_rank
  FOR INSERT WITH CHECK (npo_profile_id IN (SELECT id FROM public.npo_profiles WHERE owner_user_id = auth.uid()));
CREATE POLICY grant_win_rank_own_update ON public.grant_win_rank
  FOR UPDATE USING (npo_profile_id IN (SELECT id FROM public.npo_profiles WHERE owner_user_id = auth.uid()));

CREATE POLICY grant_applications_own_select ON public.grant_applications
  FOR SELECT USING (npo_profile_id IN (SELECT id FROM public.npo_profiles WHERE owner_user_id = auth.uid()));
CREATE POLICY grant_applications_own_insert ON public.grant_applications
  FOR INSERT WITH CHECK (npo_profile_id IN (SELECT id FROM public.npo_profiles WHERE owner_user_id = auth.uid()));
CREATE POLICY grant_applications_own_update ON public.grant_applications
  FOR UPDATE USING (npo_profile_id IN (SELECT id FROM public.npo_profiles WHERE owner_user_id = auth.uid()));
