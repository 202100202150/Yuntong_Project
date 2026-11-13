import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { randomUUID } from 'node:crypto';

export const ROLE_PERMISSIONS = {
  admin: ['monitor', 'events', 'review', 'playback', 'export', 'devices', 'models', 'users', 'settings', 'audit'],
  operator: ['monitor', 'events', 'review', 'playback', 'export'],
  auditor: ['monitor', 'events', 'playback', 'export', 'audit'],
};
export function publicUser(row) {
  if (!row) return null;
  return { id: row.id, username: row.username, displayName: row.display_name, role: row.role,
    enabled: Boolean(row.enabled), permissions: ROLE_PERMISSIONS[row.role] ?? [], createdAt: row.created_at };
}
export function openProductDatabase(directory) {
  fs.mkdirSync(directory, { recursive: true });
  const db = new DatabaseSync(path.join(directory, 'airspace.sqlite'));
  db.exec(`
    PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;
    PRAGMA busy_timeout=5000;
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS users (
      id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
      display_name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events (
      id TEXT PRIMARY KEY, track_id TEXT NOT NULL, started_at INTEGER NOT NULL,
      last_seen_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      conclusion TEXT, note TEXT NOT NULL DEFAULT '', reviewed_by TEXT,
      reviewed_at INTEGER, payload TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS events_time ON events(started_at DESC);
    CREATE INDEX IF NOT EXISTS events_track ON events(track_id, last_seen_at DESC);
    CREATE TABLE IF NOT EXISTS audit (
      id TEXT PRIMARY KEY, time INTEGER NOT NULL, actor TEXT NOT NULL,
      action TEXT NOT NULL, detail TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS audit_time ON audit(time DESC);
    CREATE TABLE IF NOT EXISTS media (
      id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, camera_id TEXT NOT NULL,
      kind TEXT NOT NULL, started_at INTEGER NOT NULL, duration REAL NOT NULL DEFAULT 0,
      bytes INTEGER NOT NULL, annotated INTEGER NOT NULL DEFAULT 1, ready INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS media_time ON media(started_at DESC);
  `);
  const get = (key, fallback) => {
    const row = db.prepare('SELECT value FROM settings WHERE key=?').get(key);
    return row ? JSON.parse(row.value) : fallback;
  };
  const set = (key, value) => db.prepare('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').run(key, JSON.stringify(value));
  const audit = (actor, action, detail = {}) => db.prepare('INSERT INTO audit VALUES(?,?,?,?,?)').run(randomUUID(), Date.now(), actor || 'system', action, JSON.stringify(detail));
  const transaction = (fn) => { db.exec('BEGIN IMMEDIATE'); try { const value = fn(); db.exec('COMMIT'); return value; } catch (error) { db.exec('ROLLBACK'); throw error; } };
  return { db, get, set, audit, transaction, directory };
}
export function fault(status, message) { const error = new Error(message); error.status = status; return error; }
export const asyncRoute = (fn) => (req, res, next) => Promise.resolve(fn(req, res)).catch(next);
export function requiredText(value, label, max = 80) {
  if (typeof value !== 'string' || !value.trim() || value.trim().length > max) throw fault(400, `${label}格式无效`);
  return value.trim();
}
