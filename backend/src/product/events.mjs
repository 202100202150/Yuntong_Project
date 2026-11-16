import { Router } from 'express';
import { randomUUID } from 'node:crypto';
import { asyncRoute, fault } from './database.mjs';
import { permission } from './auth.mjs';
import { mediaItem } from './media.mjs';

export function eventRecord(row) {
  return { id: row.id, trackId: row.track_id, startedAt: row.started_at, lastSeenAt: row.last_seen_at,
    status: row.status, conclusion: row.conclusion, note: row.note, reviewedBy: row.reviewed_by,
    reviewedAt: row.reviewed_at, payload: JSON.parse(row.payload) };
}
export function ingestTargets(store, status, time = Date.now()) {
  const created = [];
  if (!status.detecting || status.serviceState !== 'ready') return created;
  for (const target of status.spatial?.targets || []) {
    if (target.trackingState === 'held') continue;
    const row = store.db.prepare('SELECT * FROM events WHERE track_id=? AND last_seen_at>? ORDER BY last_seen_at DESC LIMIT 1').get(target.id, time - 60_000);
    const previous = row ? JSON.parse(row.payload) : { trajectory: [] };
    const point = { time, x: target.x, y: target.y, z: target.z };
    const trajectory = [...previous.trajectory, point].slice(-600);
    const payload = JSON.stringify({ ...previous, target, trajectory, cameras: Object.fromEntries(Object.entries(status.cameras).map(([id, camera]) => [id, camera.objects])) });
    if (row) store.db.prepare('UPDATE events SET last_seen_at=?,payload=? WHERE id=?').run(time, payload, row.id);
    else {
      const id = randomUUID();
      store.db.prepare('INSERT INTO events(id,track_id,started_at,last_seen_at,payload) VALUES(?,?,?,?,?)').run(id, target.id, time, time, payload);
      store.audit('system', '生成目标事件', { id, trackId: target.id });
      created.push(id);
    }
  }
  return created;
}
export function createEventsRouter(store) {
  const router = Router();
  const detail = (id) => {
    const row = store.db.prepare('SELECT * FROM events WHERE id=?').get(id);
    if (!row) throw fault(404, '事件不存在');
    const media = store.db.prepare('SELECT * FROM media WHERE ready=1 AND started_at<=? AND (started_at + duration*1000)>=?').all(row.last_seen_at + 5000, row.started_at - 5000);
    return { ...eventRecord(row), media: media.map(mediaItem) };
  };
  router.get('/events', permission('events'), (req, res) => {
    const where = []; const args = [];
    if (['pending', 'reviewed'].includes(req.query.status)) { where.push('status=?'); args.push(req.query.status); }
    if (req.query.q) { where.push('(track_id LIKE ? OR id LIKE ?)'); const q = '%' + String(req.query.q).slice(0,80) + '%'; args.push(q,q); }
    const clause = where.length ? ' WHERE ' + where.join(' AND ') : '';
    const limit = Math.min(200, Math.max(1, Number(req.query.limit) || 50));
    const offset = Math.max(0, Number(req.query.offset) || 0);
    const rows = store.db.prepare('SELECT * FROM events' + clause + ' ORDER BY started_at DESC LIMIT ? OFFSET ?').all(...args,limit,offset);
    res.json({ events: rows.map(eventRecord), total: store.db.prepare('SELECT COUNT(*) AS n FROM events'+clause).get(...args).n, pending: store.db.prepare("SELECT COUNT(*) AS n FROM events WHERE status='pending'").get().n });
  });
  router.get('/events/:id', permission('events'), (req,res,next) => { try { res.json({event:detail(req.params.id)}); } catch(error) {next(error);} });
  router.post('/events/:id/review', permission('review'), asyncRoute(async (req,res) => {
    const conclusion=req.body?.conclusion;
    if (!['drone','other','uncertain'].includes(conclusion)) throw fault(400,'请选择复核结论');
    const note=typeof req.body?.note==='string'?req.body.note.trim():'';
    if(note.length>2000) throw fault(400,'备注最多 2000 字');
    store.transaction(()=>{
      const event=detail(req.params.id);
      if(event.status==='reviewed') throw fault(409,'该事件已复核，请刷新查看结果');
      store.db.prepare("UPDATE events SET status='reviewed',conclusion=?,note=?,reviewed_by=?,reviewed_at=? WHERE id=?").run(conclusion,note,req.user.username,Date.now(),event.id);
      store.audit(req.user.username,'复核事件',{eventId:event.id,trackId:event.trackId,conclusion});
    });
    res.json({event:detail(req.params.id)});
  }));
  router.get('/events/:id/export', permission('export'), (req,res,next)=>{
    try { const event=detail(req.params.id); store.audit(req.user.username,'导出事件证据',{eventId:event.id}); res.attachment('event-'+event.id+'.json').json(event); } catch(error){next(error);}
  });
  router.get('/audit',permission('audit'),(req,res)=>{
    const limit=Math.min(200,Math.max(1,Number(req.query.limit)||50));
    res.json({entries:store.db.prepare('SELECT * FROM audit ORDER BY time DESC LIMIT ?').all(limit).map(row=>({...row,detail:JSON.parse(row.detail)}))});
  });
  return router;
}
