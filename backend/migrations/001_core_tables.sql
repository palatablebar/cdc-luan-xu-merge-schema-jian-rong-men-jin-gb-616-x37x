-- 参考 DDL（运行时由 SQLAlchemy create_all 建表，此文件用于人工核对 MySQL 8.0 结构）
CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(160) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  display_name VARCHAR(80) NOT NULL,
  role VARCHAR(20) NOT NULL,
  org VARCHAR(120) NOT NULL,
  created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS source_tables (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(40) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  database_name VARCHAR(80) NOT NULL DEFAULT 'biz_prod',
  table_name VARCHAR(80) NOT NULL,
  pk_column VARCHAR(60) NOT NULL DEFAULT 'id',
  schema_columns JSON NOT NULL,
  binlog_file VARCHAR(120),
  binlog_position INT NOT NULL DEFAULT 0,
  allowed_lateness_seconds INT NOT NULL DEFAULT 30,
  owner VARCHAR(80) NOT NULL,
  note TEXT,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  last_event_at DATETIME,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cdc_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  table_id INT NOT NULL,
  pk_value VARCHAR(80) NOT NULL,
  lsn INT NOT NULL,
  seq INT NOT NULL DEFAULT 0,
  op VARCHAR(4) NOT NULL,
  event_time DATETIME NOT NULL,
  ingest_at DATETIME NOT NULL,
  before_data JSON NOT NULL,
  after_data JSON NOT NULL,
  is_late TINYINT(1) NOT NULL DEFAULT 0,
  late_seconds INT NOT NULL DEFAULT 0,
  is_duplicate TINYINT(1) NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL DEFAULT 'applied',
  created_at DATETIME NOT NULL,
  CONSTRAINT uq_cdc_events_table_pk_lsn UNIQUE (table_id, pk_value, lsn, seq),
  KEY ix_cdc_events_table_id (table_id),
  KEY ix_cdc_events_event_time (event_time),
  KEY ix_cdc_events_ingest_at (ingest_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS merge_runs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  run_no VARCHAR(40) NOT NULL UNIQUE,
  table_id INT,
  trigger VARCHAR(20) NOT NULL DEFAULT 'manual',
  status VARCHAR(20) NOT NULL DEFAULT 'queued',
  operator_email VARCHAR(160) NOT NULL,
  allowed_lateness_seconds INT NOT NULL DEFAULT 30,
  events_in INT NOT NULL DEFAULT 0,
  upsert_rows INT NOT NULL DEFAULT 0,
  deleted_rows INT NOT NULL DEFAULT 0,
  late_count INT NOT NULL DEFAULT 0,
  dup_skipped INT NOT NULL DEFAULT 0,
  rejected_count INT NOT NULL DEFAULT 0,
  watermark_at DATETIME,
  metrics JSON NOT NULL,
  log_text TEXT,
  seed_key VARCHAR(120) UNIQUE,
  started_at DATETIME NOT NULL,
  finished_at DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS reject_rows (
  id INT AUTO_INCREMENT PRIMARY KEY,
  run_id INT NOT NULL,
  table_id INT,
  pk_value VARCHAR(80) NOT NULL,
  op VARCHAR(4) NOT NULL DEFAULT 'u',
  reason VARCHAR(28) NOT NULL,
  event_lsn INT NOT NULL DEFAULT 0,
  event_time DATETIME,
  detail JSON NOT NULL,
  replay_status VARCHAR(16) NOT NULL DEFAULT 'pending',
  replay_note TEXT,
  reviewer VARCHAR(160),
  created_at DATETIME NOT NULL,
  CONSTRAINT uq_reject_rows_natural_key UNIQUE (run_id, table_id, pk_value, event_lsn, reason)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS schema_changes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  table_id INT NOT NULL,
  change_key VARCHAR(120) NOT NULL UNIQUE,
  kind VARCHAR(24) NOT NULL,
  column_name VARCHAR(60),
  target_column VARCHAR(60),
  before_columns JSON NOT NULL,
  after_columns JSON NOT NULL,
  compat VARCHAR(16) NOT NULL,
  severity VARCHAR(12) NOT NULL DEFAULT 'low',
  action VARCHAR(12) NOT NULL DEFAULT 'apply',
  rollback_sql TEXT NOT NULL,
  summary TEXT NOT NULL,
  detail JSON NOT NULL,
  gate_status VARCHAR(16) NOT NULL DEFAULT 'pending',
  reviewer VARCHAR(160),
  review_note TEXT,
  detected_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  actor_email VARCHAR(160) NOT NULL,
  actor_role VARCHAR(20) NOT NULL,
  action VARCHAR(64) NOT NULL,
  entity VARCHAR(40) NOT NULL,
  entity_id VARCHAR(64),
  detail JSON NOT NULL,
  layer VARCHAR(16) NOT NULL DEFAULT 'domain',
  ip VARCHAR(64),
  seed_key VARCHAR(120) UNIQUE,
  created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
