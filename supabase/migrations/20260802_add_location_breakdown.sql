-- Migration: Add location breakdown fields and grant location requirement type
ALTER TABLE public.npo_profiles
  ADD COLUMN IF NOT EXISTS headquarter_location TEXT,
  ADD COLUMN IF NOT EXISTS branch_locations TEXT[] DEFAULT '{}'::TEXT[],
  ADD COLUMN IF NOT EXISTS activity_areas TEXT[] DEFAULT '{}'::TEXT[];

ALTER TABLE public.grants
  ADD COLUMN IF NOT EXISTS location_requirement_type TEXT DEFAULT 'BRANCH_ALLOWED';

CREATE INDEX IF NOT EXISTS idx_grants_location_req_type ON public.grants(location_requirement_type);
