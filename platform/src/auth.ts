/**
 * Clep auth — D1-backed accounts + self-serve API keys.
 *
 * Port of platform/auth.py (Firestore) onto D1 + Web Crypto. Same shapes on
 * purpose: token is "user_id.expiry.sig" (HMAC-SHA256 over "user_id:expiry"),
 * API key is "clep_live_<40 hex chars>" — none of that needed to change
 * just because the store did, and keeping it identical means the
 * CLI/dashboard need zero changes.
 *
 * Password hashing is PBKDF2-SHA256 at 100k iterations, not the old auth.py's
 * 200k: workerd's crypto.subtle caps PBKDF2 at 100_000 iterations and throws
 * above that (Miniflare's local dev runtime doesn't enforce this, so 200k
 * only fails once deployed — confirmed against the live Worker).
 *
 * users.email is the primary key, so D1's UNIQUE constraint gives the same
 * atomic "email already taken" rejection Firestore's doc.create() gave.
 */

import type { Env } from "./types";

const PBKDF2_ITERATIONS = 100_000;
const TOKEN_TTL_SECONDS = 30 * 24 * 3600; // 30 days
const API_KEY_CACHE_TTL_MS = 30_000;

export class AuthError extends Error {}

interface UserRow {
  email: string;
  name: string;
  password_salt: string;
  password_hash: string;
  api_key: string;
  created: number;
}

export interface PublicUser {
  email: string;
  name: string;
  api_key: string;
}

function bytesToBase64(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function pbkdf2(password: string, salt: Uint8Array): Promise<Uint8Array> {
  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: salt as BufferSource, iterations: PBKDF2_ITERATIONS },
    keyMaterial,
    256,
  );
  return new Uint8Array(bits);
}

async function hashPassword(password: string, salt?: Uint8Array): Promise<{ saltB64: string; hashB64: string }> {
  const s = salt ?? crypto.getRandomValues(new Uint8Array(16));
  const digest = await pbkdf2(password, s);
  return { saltB64: bytesToBase64(s), hashB64: bytesToBase64(digest) };
}

async function verifyPassword(password: string, saltB64: string, hashB64: string): Promise<boolean> {
  const salt = base64ToBytes(saltB64);
  const { hashB64: candidate } = await hashPassword(password, salt);
  return timingSafeEqual(candidate, hashB64);
}

async function hmacSha256Hex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return bytesToHex(new Uint8Array(sig));
}

async function makeToken(userId: string, authSecret: string): Promise<string> {
  const expiry = Math.floor(Date.now() / 1000) + TOKEN_TTL_SECONDS;
  const payload = `${userId}:${expiry}`;
  const sig = await hmacSha256Hex(authSecret, payload);
  return `${userId}.${expiry}.${sig}`;
}

async function verifyToken(token: string, authSecret: string): Promise<string | null> {
  // Split from the right: user_id is an email and always contains its own
  // "." (the domain's TLD) — only expiry/sig are guaranteed dot-free.
  const parts = token.split(".");
  if (parts.length < 3) return null;
  const sig = parts.pop()!;
  const expiryS = parts.pop()!;
  const userId = parts.join(".");
  const expiry = Number(expiryS);
  if (!Number.isFinite(expiry)) return null;
  if (Date.now() / 1000 > expiry) return null;
  const expected = await hmacSha256Hex(authSecret, `${userId}:${expiry}`);
  if (!timingSafeEqual(expected, sig)) return null;
  return userId;
}

function newApiKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(20));
  return `clep_live_${bytesToHex(bytes)}`;
}

function toPublic(row: UserRow): PublicUser {
  return { email: row.email, name: row.name ?? "", api_key: row.api_key };
}

export async function createUser(
  db: D1Database,
  authSecret: string,
  email: string,
  password: string,
  name: string,
): Promise<{ token: string; api_key: string; name: string }> {
  const docId = email.trim().toLowerCase();
  const { saltB64, hashB64 } = await hashPassword(password);
  const apiKey = newApiKey();
  try {
    await db
      .prepare(
        "INSERT INTO users (email, name, password_salt, password_hash, api_key, created) VALUES (?, ?, ?, ?, ?, ?)",
      )
      .bind(docId, name, saltB64, hashB64, apiKey, Date.now() / 1000)
      .run();
  } catch (e) {
    const msg = String(e instanceof Error ? e.message : e).toLowerCase();
    if (msg.includes("unique") || msg.includes("constraint")) {
      throw new AuthError("an account with that email already exists");
    }
    throw e;
  }
  return { token: await makeToken(docId, authSecret), api_key: apiKey, name };
}

export async function verifyLogin(
  db: D1Database,
  authSecret: string,
  email: string,
  password: string,
): Promise<{ token: string; api_key: string; name: string } | null> {
  const docId = email.trim().toLowerCase();
  const row = await db.prepare("SELECT * FROM users WHERE email = ?").bind(docId).first<UserRow>();
  if (!row) return null;
  if (!(await verifyPassword(password, row.password_salt, row.password_hash))) return null;
  return { token: await makeToken(docId, authSecret), api_key: row.api_key, name: row.name ?? "" };
}

export async function getUserByToken(db: D1Database, authSecret: string, token: string): Promise<PublicUser | null> {
  const userId = await verifyToken(token, authSecret);
  if (!userId) return null;
  const row = await db.prepare("SELECT * FROM users WHERE email = ?").bind(userId).first<UserRow>();
  if (!row) return null;
  return toPublic(row);
}

// Per-isolate cache, mirroring _API_KEY_CACHE_TTL in the old auth.py — avoids
// a D1 read on every /api/* call from a warm isolate.
const apiKeyCache = new Map<string, { at: number; user: PublicUser | null }>();

export async function getUserByApiKey(db: D1Database, apiKey: string): Promise<PublicUser | null> {
  const cached = apiKeyCache.get(apiKey);
  if (cached && Date.now() - cached.at < API_KEY_CACHE_TTL_MS) return cached.user;
  const row = await db.prepare("SELECT * FROM users WHERE api_key = ?").bind(apiKey).first<UserRow>();
  const user = row ? toPublic(row) : null;
  apiKeyCache.set(apiKey, { at: Date.now(), user });
  return user;
}

export async function isAuthorized(env: Env, headers: Headers): Promise<boolean> {
  const allowed = new Set(
    (env.CLEP_API_KEYS || "")
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean),
  );
  if (allowed.size === 0) return true;
  const apiKey = (headers.get("X-API-Key") || "").trim();
  let bearer = (headers.get("Authorization") || "").trim();
  if (bearer.toLowerCase().startsWith("bearer ")) bearer = bearer.slice(7).trim();
  for (const key of [apiKey, bearer]) {
    if (!key) continue;
    if (allowed.has(key)) return true;
    if (await getUserByApiKey(env.DB, key)) return true;
  }
  return false;
}
