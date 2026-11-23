-- Fields the digest parser can actually fill, learned from the real corpus.
--
-- Two of the 164 .tmp files are hand-written prose rather than the ECC
-- template: they carry a **Topic:** line and several hundred words of
-- narrative, and they are the most valuable content in the directory. They get
-- is_curated = 1 so recall can rank them above generated digests.
--
-- message_count is the digest-side counterpart of sessions.tool_count: the one
-- number in the file that genuinely describes the work, rather than being
-- scraped from injected context.

ALTER TABLE day_digests ADD COLUMN topic TEXT;
ALTER TABLE day_digests ADD COLUMN message_count INTEGER DEFAULT 0;
ALTER TABLE day_digests ADD COLUMN is_curated INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_day_digests_curated
    ON day_digests(is_curated) WHERE is_curated = 1;
