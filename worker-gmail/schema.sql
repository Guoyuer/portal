CREATE TABLE IF NOT EXISTS triaged_emails (
  msg_id        TEXT PRIMARY KEY,
  received_at   TEXT NOT NULL,
  classified_at TEXT NOT NULL,
  sender        TEXT NOT NULL,
  subject       TEXT NOT NULL,
  summary       TEXT NOT NULL,
  category      TEXT NOT NULL
                CHECK (category IN ('IMPORTANT','NEUTRAL','TRASH_CANDIDATE')),
  status        TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','trashed'))
);

CREATE INDEX IF NOT EXISTS idx_triaged_classified_at
  ON triaged_emails(classified_at DESC);
CREATE INDEX IF NOT EXISTS idx_triaged_category_status
  ON triaged_emails(category, status);
