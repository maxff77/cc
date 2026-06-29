CREATE TABLE IF NOT EXISTS emails (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at TEXT NOT NULL DEFAULT (datetime('now')),
  from_addr   TEXT,
  to_addr     TEXT,
  subject     TEXT,
  text        TEXT,
  html        TEXT
);
CREATE INDEX IF NOT EXISTS idx_emails_to ON emails(to_addr, id DESC);
