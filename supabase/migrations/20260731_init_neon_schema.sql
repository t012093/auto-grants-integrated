-- =============================================================================
-- Neon PostgreSQL Full Schema Initialization
-- Target: Neon (PostgreSQL 15+ with pgvector)
-- Date: 2026-07-31
-- =============================================================================

-- 1. 拡張機能の無効化/有効化
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 2. 共通Enum型
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'app_user_role') THEN
        CREATE TYPE public.app_user_role AS ENUM ('ADMIN', 'MEMBER', 'GUEST');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'grant_category') THEN
        CREATE TYPE public.grant_category AS ENUM ('PUBLIC', 'PRIVATE', 'DONATION_CF');
    END IF;
END $$;

-- 3. 共通トリガー関数 (updated_at)
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = TIMEZONE('utc', NOW());
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4. コアテーブル群

-- プロフィール
CREATE TABLE IF NOT EXISTS public.profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  full_name TEXT NOT NULL DEFAULT '',
  avatar_url TEXT,
  role public.app_user_role NOT NULL DEFAULT 'MEMBER',
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);

-- NPOプロファイル
CREATE TABLE IF NOT EXISTS public.npo_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id UUID REFERENCES public.profiles (id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  email TEXT,
  website TEXT,
  location TEXT,
  description TEXT NOT NULL DEFAULT '',
  needed_resources TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  activity_tags TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  target_audience TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  organization_type TEXT NOT NULL DEFAULT 'NPO_CORPORATION',
  establishment_year INTEGER,
  annual_budget BIGINT,
  prepared_documents TEXT[] DEFAULT '{}'::TEXT[],
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);

-- 企業プロファイル
CREATE TABLE IF NOT EXISTS public.company_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id UUID REFERENCES public.profiles (id) ON DELETE CASCADE,
  company_name TEXT NOT NULL,
  industry TEXT NOT NULL,
  employee_band TEXT,
  contact_email TEXT,
  mission TEXT,
  website TEXT,
  description TEXT NOT NULL DEFAULT '',
  resources TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  focus_areas TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  has_volunteer_leave BOOLEAN NOT NULL DEFAULT false,
  has_matching_gift BOOLEAN NOT NULL DEFAULT false,
  guidelines TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);

-- メンバー
CREATE TABLE IF NOT EXISTS public.members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_profile_id UUID REFERENCES public.company_profiles (id) ON DELETE CASCADE,
  npo_profile_id UUID REFERENCES public.npo_profiles (id) ON DELETE CASCADE,
  user_id UUID REFERENCES public.profiles (id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  role public.app_user_role NOT NULL,
  department TEXT,
  avatar_url TEXT,
  bio TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  CONSTRAINT chk_members_org_exclusivity CHECK (
    (company_profile_id IS NOT NULL AND npo_profile_id IS NULL) OR
    (company_profile_id IS NULL AND npo_profile_id IS NOT NULL)
  )
);

-- プロジェクト
CREATE TABLE IF NOT EXISTS public.projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL,
  created_by UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  npo_profile_id UUID REFERENCES public.npo_profiles(id) ON DELETE CASCADE,
  company_profile_id UUID REFERENCES public.company_profiles(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc', NOW())
);

-- 5. 助成金・パイプラインテーブル群

-- 助成金データ本体
CREATE TABLE IF NOT EXISTS public.grants (
  id SERIAL PRIMARY KEY,
  source_grant_id TEXT NOT NULL,
  title TEXT NOT NULL,
  provider TEXT NOT NULL,
  amount_min BIGINT,
  amount_max BIGINT,
  deadline DATE,
  details_url TEXT,
  target_area TEXT DEFAULT '全国',
  is_rate_10_10 BOOLEAN DEFAULT FALSE,
  is_advance_payment BOOLEAN DEFAULT FALSE,
  eligible_org_types TEXT[] DEFAULT '{NPO_CORPORATION, GENERAL_INC, UNINCORPORATED}',
  min_years_active INTEGER DEFAULT 0,
  required_documents TEXT[] DEFAULT '{}',
  detail_text TEXT,
  attachment_urls TEXT[] DEFAULT '{}',
  is_ocr_processed BOOLEAN DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'OPEN',
  category public.grant_category NOT NULL DEFAULT 'PUBLIC',
  source TEXT NOT NULL,
  payload_json JSONB NOT NULL DEFAULT '{}'::JSONB,
  cascade_id INTEGER,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT uq_grants_source_grant_id UNIQUE (source, source_grant_id)
);

CREATE INDEX IF NOT EXISTS idx_grants_category ON public.grants(category);
CREATE INDEX IF NOT EXISTS idx_grants_source ON public.grants(source);
CREATE INDEX IF NOT EXISTS idx_grants_status ON public.grants(status);
CREATE INDEX IF NOT EXISTS idx_grants_rate_10_10 ON public.grants (is_rate_10_10) WHERE is_rate_10_10 = TRUE;
CREATE INDEX IF NOT EXISTS idx_grants_advance_payment ON public.grants (is_advance_payment) WHERE is_advance_payment = TRUE;
CREATE INDEX IF NOT EXISTS idx_grants_target_area ON public.grants(target_area);
CREATE INDEX IF NOT EXISTS idx_grants_amount_max ON public.grants(amount_max);
CREATE INDEX IF NOT EXISTS idx_grants_payload_json_gin ON public.grants USING GIN (payload_json);

-- ベクトル知識チャンク (bge-base-ja-v1.5 768次元)
CREATE TABLE IF NOT EXISTS public.knowledge_chunks (
  id SERIAL PRIMARY KEY,
  grant_id INTEGER REFERENCES public.grants(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  embedding vector(768) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding_hnsw
ON public.knowledge_chunks USING hnsw (embedding vector_cosine_ops);

-- 過去採択事例データ (past_award_analyzer 用)
CREATE TABLE IF NOT EXISTS public.grant_past_awards (
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

CREATE INDEX IF NOT EXISTS idx_grant_past_awards_funder ON public.grant_past_awards(funder_name);
CREATE INDEX IF NOT EXISTS idx_grant_past_awards_year ON public.grant_past_awards(award_year);

-- 助成金別 経費ルール (grant_expense_validator 用)
CREATE TABLE IF NOT EXISTS public.grant_expense_rules (
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

CREATE INDEX IF NOT EXISTS idx_grant_expense_rules_grant ON public.grant_expense_rules(grant_id);

-- 団体の経費希望優先度 (動的配分 Solver 用)
CREATE TABLE IF NOT EXISTS public.npo_expense_preferences (
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

CREATE INDEX IF NOT EXISTS idx_npo_expense_pref_profile ON public.npo_expense_preferences(npo_profile_id);

-- 適合通知アラート (grant_matching_engine / grant_lifecycle_manager 用)
CREATE TABLE IF NOT EXISTS public.alerts (
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

CREATE INDEX IF NOT EXISTS idx_alerts_npo_profile ON public.alerts(npo_profile_id);
CREATE INDEX IF NOT EXISTS idx_alerts_unread ON public.alerts(is_read) WHERE is_read = FALSE;

-- 6. トリガー設定
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_profiles_updated_at') THEN
        CREATE TRIGGER set_profiles_updated_at BEFORE UPDATE ON public.profiles FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_npo_profiles_updated_at') THEN
        CREATE TRIGGER set_npo_profiles_updated_at BEFORE UPDATE ON public.npo_profiles FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_company_profiles_updated_at') THEN
        CREATE TRIGGER set_company_profiles_updated_at BEFORE UPDATE ON public.company_profiles FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_members_updated_at') THEN
        CREATE TRIGGER set_members_updated_at BEFORE UPDATE ON public.members FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_projects_updated_at') THEN
        CREATE TRIGGER set_projects_updated_at BEFORE UPDATE ON public.projects FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_grants_updated_at') THEN
        CREATE TRIGGER set_grants_updated_at BEFORE UPDATE ON public.grants FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_npo_expense_pref_updated_at') THEN
        CREATE TRIGGER set_npo_expense_pref_updated_at BEFORE UPDATE ON public.npo_expense_preferences FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();
    END IF;
END $$;
