export interface Env {
  DB: D1Database;
  OUTPUTS: R2Bucket;
  CLEP_JOBS: Queue;

  CLEP_API_KEYS: string;
  CLOUD_RUN_SCAN_URL: string;
  CLEP_INTERNAL_SECRET: string;
  CLEP_AUTH_SECRET: string;
}

export interface JobRow {
  id: string;
  status: string;
  created: number;
  url: string | null;
  name: string | null;
  prompt: string | null;
  kind: string | null;
  style: string | null;
  quality: string | null;
  out: string | null;
  error: string | null;
  trace: string | null;
}

export interface FeatureRow {
  key: string;
  url: string;
  name: string | null;
  title: string | null;
  element: string | null;
  component_json: string;
  interactions_json: string;
  states_json: string;
  has_input: number | null;
  has_button: number | null;
  button_label: string;
  updated: string;
  source: string;
}
