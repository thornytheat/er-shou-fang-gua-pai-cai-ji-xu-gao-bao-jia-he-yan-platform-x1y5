-- 0002：行情版本（可发布、可回溯的行情快照）
-- 与 src/models/market_version.py 一一对应；运行时仍由 create_all 建表。

CREATE TABLE IF NOT EXISTS market_versions (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  version_no    VARCHAR(16)  NOT NULL,
  seq           INT          NOT NULL DEFAULT 0,
  status        VARCHAR(12)  NOT NULL DEFAULT 'draft',
  title         VARCHAR(80)  NULL,
  note          TEXT         NULL,
  row_count     INT          NOT NULL DEFAULT 0,
  flag_counts   JSON         NULL,
  input_hash    VARCHAR(64)  NOT NULL,
  csv_hash      VARCHAR(64)  NOT NULL DEFAULT '',
  snapshot      JSON         NULL,
  published_by  VARCHAR(160) NULL,
  published_at  DATETIME     NULL,
  retracted_by  VARCHAR(160) NULL,
  retracted_at  DATETIME     NULL,
  retract_note  TEXT         NULL,
  created_at    DATETIME     NOT NULL,
  updated_at    DATETIME     NOT NULL,
  UNIQUE KEY uq_market_version_no (version_no),
  KEY ix_market_versions_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
