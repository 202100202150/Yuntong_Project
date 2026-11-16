import { Router } from 'express';
import { randomBytes, randomUUID, createHash, scrypt, timingSafeEqual } from 'node:crypto';
import { promisify } from 'node:util';
import { asyncRoute, fault, publicUser, requiredText, ROLE_PERMISSIONS } from './database.mjs';

const derive = promisify(scrypt);
const COOKIE = 'yuntong_session';
const SESSION_MS = 12 * 60 * 60 * 1000;
const digest = (value) => createHash('sha256').update(value).digest('hex');
export function readSiteName(store) {
  const value = store.get('general', {})?.siteName;
  return typeof value === 'string' && value.trim() ? value.trim() : '校园监测';
}
export async function hashPassword(password) {
  if (typeof password !== 'string' || password.length < 8 || password.length > 128) throw fault(400, '密码长度应为 8–128 个字符');
  const salt = randomBytes(16).toString('hex');
  const hash = await derive(password, salt, 64, { N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 });
  return salt + ':' + hash.toString('hex');
}
async function verify(password, stored) {
  if (typeof password !== 'string' || password.length > 128 || !stored) return false;
  const [salt, expected] = stored.split(':');
  const actual = await derive(password, salt, 64, { N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 });
  const buffer = Buffer.from(expected, 'hex');
  return actual.length === buffer.length && timingSafeEqual(actual, buffer);
}
function cookieToken(req) {
  const value = (req.headers.cookie || '').split(';').find(item => item.trim().startsWith(COOKIE + '='))?.trim().slice(COOKIE.length + 1);
  return value && /^[a-f0-9]{64}$/.test(value) ? value : null;
}
export function createAuth(store) {
  const router = Router();
  const attempts = new Map();
  const sessionUser = (req) => {
    const token = cookieToken(req);
    if (!token) return null;
    const row = store.db.prepare('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1').get(digest(token), Date.now());
    return publicUser(row);
  };
  const clearCookie = (res) => res.clearCookie(COOKIE, { httpOnly: true, sameSite: 'strict', path: '/' });
  const login = (req, res, user) => {
    const token = randomBytes(32).toString('hex');
    store.db.prepare('DELETE FROM sessions WHERE expires_at<=?').run(Date.now());
    store.db.prepare('INSERT INTO sessions VALUES(?,?,?)').run(digest(token), user.id, Date.now() + SESSION_MS);
    res.cookie(COOKIE, token, { httpOnly: true, sameSite: 'strict', secure: req.secure, path: '/', maxAge: SESSION_MS });
    return publicUser(user);
  };
  const throttle = (req, _res, next) => {
    const key = req.socket.remoteAddress || 'unknown';
    const now = Date.now();
    for (const [address, value] of attempts) if (now - value.start > 15 * 60_000) attempts.delete(address);
    const value = attempts.get(key) ?? { start: now, count: 0 };
    if (value.count >= 10) return next(fault(429, '尝试次数过多，请稍后再试'));
    value.count++; attempts.set(key, value); next();
  };
  router.get('/status', (req, res) => {
    res.set('Cache-Control', 'no-store').json({ setupRequired: store.db.prepare('SELECT COUNT(*) AS count FROM users').get().count === 0, user: sessionUser(req), siteName: readSiteName(store) });
  });
  router.post('/setup', throttle, asyncRoute(async (req, res) => {
    const address = req.socket.remoteAddress || '';
    if (!['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(address)) throw fault(403, '请在服务所在电脑上创建首个管理员');
    const hostname = (req.hostname || '').replace(/^\[|\]$/g, '');
    if (!['localhost', '127.0.0.1', '::1'].includes(hostname) || req.headers.forwarded || req.headers['x-forwarded-for'] || req.headers['x-real-ip']) throw fault(403, '首次初始化请直接访问本机地址，不可通过反向代理');
    const username = requiredText(req.body?.username, '账号', 40);
    if (!/^[a-zA-Z0-9._-]{3,40}$/.test(username)) throw fault(400, '账号应为 3–40 位字母、数字或 . _ -');
    const displayName = requiredText(req.body?.displayName || '系统管理员', '显示名称');
    const passwordHash = await hashPassword(req.body?.password);
    const user = store.transaction(() => {
      if (store.db.prepare('SELECT COUNT(*) AS count FROM users').get().count) throw fault(409, '管理员已创建，请直接登录');
      const row = { id: randomUUID(), username, display_name: displayName, password_hash: passwordHash, role: 'admin', enabled: 1, created_at: Date.now() };
      store.db.prepare('INSERT INTO users VALUES(?,?,?,?,?,?,?)').run(row.id, row.username, row.display_name, row.password_hash, row.role, row.enabled, row.created_at);
      store.audit(username, '创建初始管理员');
      return row;
    });
    res.status(201).json({ user: login(req, res, user) });
  }));
  router.post('/login', throttle, asyncRoute(async (req, res) => {
    const username = typeof req.body?.username === 'string' ? req.body.username.trim().slice(0, 40) : '';
    const user = store.db.prepare('SELECT * FROM users WHERE username=?').get(username);
    if (!user || !user.enabled || !await verify(req.body?.password, user.password_hash)) throw fault(401, '账号或密码错误');
    attempts.delete(req.socket.remoteAddress || 'unknown');
    store.audit(user.username, '登录系统');
    res.json({ user: login(req, res, user) });
  }));
  router.post('/logout', (req, res) => {
    const token = cookieToken(req); const user = sessionUser(req);
    if (token) store.db.prepare('DELETE FROM sessions WHERE token_hash=?').run(digest(token));
    if (user) store.audit(user.username, '退出登录');
    clearCookie(res); res.json({ ok: true });
  });
  const requireSession = (req, _res, next) => {
    req.user = sessionUser(req);
    if (!req.user) return next(fault(401, '请先登录'));
    next();
  };
  return { router, requireSession, sessionUser };
}
export const permission = (name) => (req, _res, next) => req.user?.permissions.includes(name) ? next() : next(fault(403, '当前账号没有此操作权限'));
export function createUsersRouter(store) {
  const router = Router();
  router.use(permission('users'));
  router.get('/', (_req, res) => res.json({ users: store.db.prepare('SELECT * FROM users ORDER BY created_at').all().map(publicUser) }));
  router.post('/', asyncRoute(async (req, res) => {
    const username = requiredText(req.body?.username, '账号', 40);
    if (!/^[a-zA-Z0-9._-]{3,40}$/.test(username)) throw fault(400, '账号格式无效');
    const name = requiredText(req.body?.displayName, '显示名称');
    const role = req.body?.role;
    if (!ROLE_PERMISSIONS[role]) throw fault(400, '角色无效');
    const enabled = req.body?.enabled ?? true;
    if (typeof enabled !== 'boolean') throw fault(400, '账号状态无效');
    if (store.db.prepare('SELECT id FROM users WHERE username=?').get(username)) throw fault(409, '账号已存在');
    const password = await hashPassword(req.body?.password);
    const id = randomUUID();
    store.db.prepare('INSERT INTO users VALUES(?,?,?,?,?,?,?)').run(id, username, name, password, role, Number(enabled), Date.now());
    store.audit(req.user.username, '新增用户', { username, role, enabled });
    res.status(201).json({ user: publicUser(store.db.prepare('SELECT * FROM users WHERE id=?').get(id)) });
  }));
  router.patch('/:id', asyncRoute(async (req, res) => {
    const old = store.db.prepare('SELECT * FROM users WHERE id=?').get(req.params.id);
    if (!old) throw fault(404, '用户不存在');
    const name = requiredText(req.body?.displayName ?? old.display_name, '显示名称');
    const role = req.body?.role ?? old.role;
    const enabled = req.body?.enabled === undefined ? Boolean(old.enabled) : req.body.enabled;
    if (!ROLE_PERMISSIONS[role] || typeof enabled !== 'boolean') throw fault(400, '角色或账号状态无效');
    const password = req.body?.password ? await hashPassword(req.body.password) : old.password_hash;
    store.transaction(() => {
      if (old.role === 'admin' && old.enabled && (role !== 'admin' || !enabled) &&
        store.db.prepare("SELECT COUNT(*) AS count FROM users WHERE role='admin' AND enabled=1").get().count <= 1) throw fault(409, '至少需要保留一位启用的管理员');
      store.db.prepare('UPDATE users SET display_name=?,role=?,enabled=?,password_hash=? WHERE id=?').run(name, role, Number(enabled), password, old.id);
      if (!enabled || password !== old.password_hash || role !== old.role) store.db.prepare('DELETE FROM sessions WHERE user_id=?').run(old.id);
      store.audit(req.user.username, '更新用户', { username: old.username, role, enabled, passwordChanged: password !== old.password_hash });
    });
    res.json({ user: publicUser(store.db.prepare('SELECT * FROM users WHERE id=?').get(old.id)) });
  }));
  return router;
}
