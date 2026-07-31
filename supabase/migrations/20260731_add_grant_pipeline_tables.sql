-- =============================================================================
-- Migration: 助成金パイプライン拡張 (4 新規テーブル + RLS)
-- Target: Supabase (PostgreSQL 15 + pgvector)
-- Date: 2026-07-31
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. 過去採択事例データ (past_award_analyzer 用)
-- ---------------------------------------------------------------------------
CREATE TABLE public.grant_past_awards (
  id SERIAL PRIMARY KEY,
  grant_id INTEGER REFERENCES public.grants(id) ON DELETE SET NULL,
  source TEXT NOT NULL,
  funder_name TEXT NOT NULL,
  program_name TEXT NOT NULL,
  award_year INTEGER NOT NULL,
  recipient_name TEXT,
  project_title TEXT NOT NULL,
  award_amount BIGINT,
  project_summary TEXT,
  evaluation_comment TEXT,
  source_url TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_grant_past_awards_funder ON public.grant_past_awards(funder_name);
CREATE INDEX idx_grant_past_awards_year ON public.grant_past_awards(award_year);

-- ---------------------------------------------------------------------------
-- 2. 助成金別 経費ルール (grant_expense_validator 用)
-- ---------------------------------------------------------------------------
CREATE TABLE public.grant_expense_rules (
  id SERIAL PRIMARY KEY,
  grant_id INTEGER NOT NULL REFERENCES public.grants(id) ON DELETE CASCADE,
  category_code TEXT NOT NULL,
  category_label TEXT NOT NULL,
  allowed BOOLEAN NOT NULL DEFAULT TRUE,
  max_limit BIGINT,
  max_ratio NUMERIC(5,4),
  notes TEXT,
  evidence_quote TEXT,
  evidence_page TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT uq_expense_rule UNIQUE (grant_id, category_code)
);

CREATE INDEX idx_grant_expense_rules_grant ON public.grant_expense_rules(grant_id);

-- ---------------------------------------------------------------------------
-- 3. 団体の経費希望優先度 (動的配分 Solver 用)
-- ---------------------------------------------------------------------------
CREATE TABLE public.npo_expense_preferences (
  id SERIAL PRIMARY KEY,
  npo_profile_id UUID NOT NULL REFERENCES public.npo_profiles(id) ON DELETE CASCADE,
  category_code TEXT NOT NULL,
  priority INTEGER NOT NULL,
  desired_amount BIGINT,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT uq_npo_expense_pref UNIQUE (npo_profile_id, category_code)
);

CREATE INDEX idx_npo_expense_pref_profile ON public.npo_expense_preferences(npo_profile_id);

CREATE TRIGGER set_npo_expense_pref_updated_at
BEFORE UPDATE ON public.npo_expense_preferences
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- ---------------------------------------------------------------------------
-- 4. 適合通知アラート (grant_matching_engine / grant_lifecycle_manager 用)
-- ---------------------------------------------------------------------------
CREATE TABLE public.alerts (
  id SERIAL PRIMARY KEY,
  npo_profile_id UUID REFERENCES public.npo_profiles(id) ON DELETE CASCADE,
  grant_id INTEGER REFERENCES public.grants(id) ON DELETE CASCADE,
  alert_type TEXT NOT NULL DEFAULT 'ELIGIBILITY_MATCH',
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  match_score INTEGER,
  is_read BOOLEAN DEFAULT FALSE,
  is_notified BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_alerts_npo_profile ON public.alerts(npo_profile_id);
CREATE INDEX idx_alerts_unread ON public.alerts(is_read) WHERE is_read = FALSE;

-- ---------------------------------------------------------------------------
-- 5. RLS (Row Level Security) ポリシー
-- ---------------------------------------------------------------------------

-- 5.1 grant_past_awards / grant_expense_rules: 全ユーザー読み取り可、書き込みは service_role のみ
ALTER TABLE public.grant_past_awards ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.grant_expense_rules ENABLE ROW LEVEL SECURITY;

CREATE POLICY grant_past_awards_read_policy ON public.grant_past_awards FOR SELECT USING (true);
CREATE POLICY grant_expense_rules_read_policy ON public.grant_expense_rules FOR SELECT USING (true);

-- 5.2 npo_expense_preferences: 自団体の優先度のみ読み書き可能
ALTER TABLE public.npo_expense_preferences ENABLE ROW LEVEL SECURITY;

CREATE POLICY npo_expense_pref_owner_read ON public.npo_expense_preferences
  FOR SELECT USING (
    npo_profile_id IN (SELECT id FROM public.npo_profiles WHERE owner_user_id = auth.uid())
  );
CREATE POLICY npo_expense_pref_owner_write ON public.npo_expense_preferences
  FOR ALL USING (
    npo_profile_id IN (SELECT id FROM public.npo_profiles WHERE owner_user_id = auth.uid())
  );

-- 5.3 alerts: 自団体向けアラートのみ閲覧・既読更新可能
ALTER TABLE public.alerts ENABLE ROW LEVEL SECURITY;

CREATE POLICY alerts_owner_read ON public.alerts
  FOR SELECT USING (
    npo_profile_id IN (SELECT id FROM public.npo_profiles WHERE owner_user_id = auth.uid())
  );
CREATE POLICY alerts_owner_update ON public.alerts
  FOR UPDATE USING (
    npo_profile_id IN (SELECT id FROM public.npo_profiles WHERE owner_user_id = auth.uid())
  );
