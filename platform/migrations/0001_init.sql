-- Users: doc-id-by-email in the old Firestore model becomes email as PK here.
CREATE TABLE users (
  email         TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  password_salt TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  api_key       TEXT NOT NULL UNIQUE,
  created       REAL NOT NULL
);

CREATE INDEX idx_users_api_key ON users(api_key);

-- Jobs: mirrors the shape platform/server.py kept in the in-memory _jobs dict.
CREATE TABLE jobs (
  id      TEXT PRIMARY KEY,
  status  TEXT NOT NULL,
  created REAL NOT NULL,
  url     TEXT,
  name    TEXT,
  prompt  TEXT,
  kind    TEXT,
  style   TEXT,
  quality TEXT,
  out     TEXT,
  error   TEXT,
  trace   TEXT
);

CREATE INDEX idx_jobs_created ON jobs(created DESC);

-- Features: one row per (url, name) pair, matching _merge_feature's
-- key = f"{url}||{feat['name']}" upsert semantics. Nested lists/objects
-- (component, interactions, states) stored as JSON text, same as the old
-- registry.json blob.
CREATE TABLE features (
  key             TEXT PRIMARY KEY,
  url             TEXT NOT NULL,
  name            TEXT,
  title           TEXT,
  element         TEXT,
  component_json  TEXT NOT NULL DEFAULT '[]',
  interactions_json TEXT NOT NULL DEFAULT '[]',
  states_json     TEXT NOT NULL DEFAULT '[]',
  has_input       INTEGER,
  has_button      INTEGER,
  button_label    TEXT NOT NULL DEFAULT '',
  updated         TEXT NOT NULL,
  source          TEXT NOT NULL
);

CREATE INDEX idx_features_updated ON features(updated DESC);
