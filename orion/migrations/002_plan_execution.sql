CREATE TABLE IF NOT EXISTS orion_plan_state (
    plan_id TEXT PRIMARY KEY REFERENCES orion_plans(plan_id),
    status TEXT NOT NULL CHECK (
        status IN (
            'blocked', 'no_action', 'pending_approval', 'approved',
            'executing', 'succeeded', 'canary_failed', 'rolled_back',
            'rollback_failed', 'cleanup_failed', 'failed'
        )
    ),
    approval_hash TEXT,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    execution_result JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
