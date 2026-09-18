/**
 * Feature registry — port of _load_registry/_merge_feature from
 * platform/server.py onto D1. Same upsert-by-key, keep-last-20-interactions,
 * trim-to-200 semantics as the old registry.json blob.
 */

import type { FeatureRow } from "./types";

const MAX_FEATURES = 200;
const MAX_INTERACTIONS = 20;

export interface FeatureRecord {
  key: string;
  url: string;
  name: string | null;
  title: string | null;
  element: string | null;
  component: unknown[];
  interactions: unknown[];
  states: unknown[];
  has_input: boolean | null;
  has_button: boolean | null;
  button_label: string;
  updated: string;
  source: string;
}

function toRecord(row: FeatureRow): FeatureRecord {
  return {
    key: row.key,
    url: row.url,
    name: row.name,
    title: row.title,
    element: row.element,
    component: JSON.parse(row.component_json || "[]"),
    interactions: JSON.parse(row.interactions_json || "[]"),
    states: JSON.parse(row.states_json || "[]"),
    has_input: row.has_input === null ? null : Boolean(row.has_input),
    has_button: row.has_button === null ? null : Boolean(row.has_button),
    button_label: row.button_label,
    updated: row.updated,
    source: row.source,
  };
}

function titleCase(name: string): string {
  return name
    .split("-")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

export async function loadRegistry(db: D1Database): Promise<{ features: FeatureRecord[] }> {
  const { results } = await db
    .prepare("SELECT * FROM features ORDER BY updated DESC LIMIT ?")
    .bind(MAX_FEATURES)
    .all<FeatureRow>();
  return { features: (results ?? []).map(toRecord) };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function mergeFeature(db: D1Database, url: string, feat: Record<string, any>, source: string): Promise<void> {
  const name: string = feat.name ?? "";
  const key = `${url}||${name}`;
  const title = feat.title || (name ? titleCase(name) : "");
  const interactions = Array.isArray(feat.interactions) ? feat.interactions.slice(-MAX_INTERACTIONS) : [];
  const component = Array.isArray(feat.component) ? feat.component : [];
  const states = Array.isArray(feat.states) ? feat.states : [];
  const updated = new Date().toISOString().replace(/\.\d+Z$/, "");

  await db
    .prepare(
      `INSERT INTO features
         (key, url, name, title, element, component_json, interactions_json, states_json, has_input, has_button, button_label, updated, source)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(key) DO UPDATE SET
         url = excluded.url, name = excluded.name, title = excluded.title, element = excluded.element,
         component_json = excluded.component_json, interactions_json = excluded.interactions_json,
         states_json = excluded.states_json, has_input = excluded.has_input, has_button = excluded.has_button,
         button_label = excluded.button_label, updated = excluded.updated, source = excluded.source`,
    )
    .bind(
      key,
      url,
      name || null,
      title || null,
      feat.element ?? null,
      JSON.stringify(component),
      JSON.stringify(interactions),
      JSON.stringify(states),
      feat.has_input === undefined || feat.has_input === null ? null : feat.has_input ? 1 : 0,
      feat.has_button === undefined || feat.has_button === null ? null : feat.has_button ? 1 : 0,
      feat.button_label || "",
      updated,
      source,
    )
    .run();

  // Trim to the 200 most recently updated, same cap _merge_feature applied.
  await db
    .prepare(
      `DELETE FROM features WHERE key NOT IN (SELECT key FROM features ORDER BY updated DESC LIMIT ?)`,
    )
    .bind(MAX_FEATURES)
    .run();
}
