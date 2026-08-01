-- =============================================================================
-- Migration: 助成金企画書・チーム連携コラボレーションテーブル (8 新規テーブル + インデックス)
-- Target: Neon PostgreSQL 15 + pgvector
-- Date: 2026-08-02
-- Spec: docs/grant_proposal_collaboration_spec.md
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. 企画書マスターテーブル (アイデア構想〜採択後まで一元管理)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.grant_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    npo_id UUID NOT NULL REFERENCES public.npo_profiles(id) ON DELETE CASCADE,

    title VARCHAR(255) NOT NULL,
    concept_summary TEXT,

    -- ai-note-meet 連携キー
    ai_note_project_id VARCHAR(100),
    ai_note_page_id VARCHAR(100),

    -- ライフサイクルステータス
    status VARCHAR(50) NOT NULL DEFAULT 'IDEA',
    -- IDEA, DRAFT, IN_REVIEW, PARTNER_MATCHING, READY, SUBMITTED, ADOPTED, REJECTED, COMPLETED

    -- 構造化コンテンツ
    content_markdown TEXT,
    budget_json JSONB DEFAULT '{}'::jsonb,
    kpi_json JSONB DEFAULT '{}'::jsonb,

    -- RAG検索用ベクトル (BGE-M3 1024次元)
    embedding vector(1024),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_proposals_npo ON public.grant_proposals(npo_id);
CREATE INDEX idx_proposals_status ON public.grant_proposals(status);

CREATE TRIGGER set_grant_proposals_updated_at
BEFORE UPDATE ON public.grant_proposals
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- ---------------------------------------------------------------------------
-- 2. 企画書 ⇔ 助成金 多対多マッピング
--    1つの企画書アイデアに複数の助成金候補を紐づけて比較検討
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.proposal_grant_mappings (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,
    grant_id INTEGER NOT NULL REFERENCES public.grants(id) ON DELETE CASCADE,

    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    match_score INTEGER,
    status VARCHAR(50) NOT NULL DEFAULT 'CONSIDERING',
    -- CONSIDERING, APPLIED, ADOPTED, REJECTED
    notes TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_proposal_grant UNIQUE (proposal_id, grant_id)
);

CREATE INDEX idx_proposal_grant_map_proposal ON public.proposal_grant_mappings(proposal_id);
CREATE INDEX idx_proposal_grant_map_grant ON public.proposal_grant_mappings(grant_id);

-- ---------------------------------------------------------------------------
-- 3. プロジェクトオファー & ポジション管理
--    プロジェクト発足時に各ポジション（PM, 現場, IT等）を定義
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.proposal_project_offers (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,

    position_code VARCHAR(50) NOT NULL,
    -- PM, LOCAL_DIR, SITE_OP, IT_CREATOR, etc.
    position_name VARCHAR(100) NOT NULL,
    capacity INTEGER NOT NULL DEFAULT 1,
    task_allocation_tag VARCHAR(20) NOT NULL DEFAULT 'HUMAN',
    -- AUTO, HYBRID, HUMAN
    compensation_notes TEXT,
    initial_tasks_json JSONB DEFAULT '[]'::jsonb,
    status VARCHAR(50) NOT NULL DEFAULT 'RECRUITING',
    -- RECRUITING, CLOSED, FILLED

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_offers_proposal ON public.proposal_project_offers(proposal_id);
CREATE INDEX idx_offers_status ON public.proposal_project_offers(status);

-- ---------------------------------------------------------------------------
-- 4. メンバー先着エントリー管理
--    メンバーがオファーに応募 → 先着順で確定
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.proposal_offer_entries (
    id BIGSERIAL PRIMARY KEY,
    offer_id BIGINT NOT NULL REFERENCES public.proposal_project_offers(id) ON DELETE CASCADE,

    applicant_name VARCHAR(255) NOT NULL,
    applicant_email VARCHAR(255),
    entry_order INTEGER NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'ACCEPTED',
    -- ACCEPTED, WAITLIST, WITHDRAWN

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_entries_offer ON public.proposal_offer_entries(offer_id);

-- ---------------------------------------------------------------------------
-- 5. 窓口コミュニケーション履歴 (アフターフォロー対応)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.proposal_communications (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,

    contact_target VARCHAR(255) NOT NULL,
    channel VARCHAR(50) NOT NULL DEFAULT 'EMAIL',
    -- EMAIL, PHONE, MEETING, LINE
    summary TEXT NOT NULL,
    full_log TEXT,
    next_action_date DATE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_comms_proposal ON public.proposal_communications(proposal_id);
CREATE INDEX idx_comms_next_action ON public.proposal_communications(next_action_date)
    WHERE next_action_date IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 6. 二重申請防止 リソース重複監視テーブル
--    同一スタッフや備品を複数助成金で100%ずつ計上する事故を防止
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.proposal_resource_allocations (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,

    resource_type VARCHAR(50) NOT NULL,
    -- STAFF_HOURS, EQUIPMENT, LOCATION
    resource_identifier VARCHAR(255) NOT NULL,
    allocated_percentage INTEGER NOT NULL DEFAULT 100
        CHECK (allocated_percentage > 0 AND allocated_percentage <= 100),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_resource_date_range CHECK (start_date <= end_date)
);

CREATE INDEX idx_resource_alloc_proposal ON public.proposal_resource_allocations(proposal_id);
CREATE INDEX idx_resource_alloc_identifier ON public.proposal_resource_allocations(resource_identifier);

-- ---------------------------------------------------------------------------
-- 7. 監査エビデンス一括保管テーブル
--    タイムシート、領収書、振込控え等をファイル単位で紐づけ管理
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.proposal_audit_evidences (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,

    evidence_type VARCHAR(50) NOT NULL,
    -- TIMESHEET, RECEIPT, BANK_TRANSFER, AGREEMENT_PDF, PRESENTATION_PDF
    file_name VARCHAR(255) NOT NULL,
    file_path VARCHAR(512) NOT NULL,
    file_size_bytes BIGINT,
    verification_status VARCHAR(50) NOT NULL DEFAULT 'UNVERIFIED',
    -- UNVERIFIED, VERIFIED, FLAGGED

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_proposal ON public.proposal_audit_evidences(proposal_id);
CREATE INDEX idx_audit_type ON public.proposal_audit_evidences(evidence_type);

-- ---------------------------------------------------------------------------
-- 8. 不採択分析 & ピボット再利用管理テーブル
--    不採択理由を学習し、別の助成金へ企画書を自動ピボット再応募
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.proposal_reviews_and_retries (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES public.grant_proposals(id) ON DELETE CASCADE,

    rejection_reason_category VARCHAR(100),
    -- BUDGET_EXCESS, REGIONAL_LINKAGE_WEAK, NOVELTY_LACK, ELIGIBILITY_MISMATCH, OTHER
    feedback_notes TEXT,
    pivot_target_grant_id INTEGER REFERENCES public.grants(id) ON DELETE SET NULL,
    pivot_status VARCHAR(50) NOT NULL DEFAULT 'ANALYZED',
    -- ANALYZED, PIVOTING, RESUBMITTED, ADOPTED

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reviews_proposal ON public.proposal_reviews_and_retries(proposal_id);
