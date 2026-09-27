'use client';
import { useEffect, useRef, useState } from 'react';
import {
  makeDemoTracks,
  mergeTrack,
  parseTrack,
  parseHealth,
  isRecord,
  type DisplayTrack,
  type Health,
  type Track,
} from './airspace';

export function useAirspace(
  mode: 'demo' | 'live',
  backend: string,
  playing: boolean,
) {
  const [tracks, setTracks] = useState<DisplayTrack[]>(() => makeDemoTracks());
  const [events, setEvents] = useState<Track[]>(() =>
    makeDemoTracks().filter((t) => t.class !== 'bird'),
  );
  const [health, setHealth] = useState<Health | null>(null);
  const [cameraIds, setCameraIds] = useState(['cam01', 'cam02']);
  const [status, setStatus] = useState<
    'demo' | 'connecting' | 'live' | 'offline'
  >('demo');
  const [error, setError] = useState('');
  const [backendDemo, setBackendDemo] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const seconds = useRef(0);
  const playingRef = useRef(playing);
  playingRef.current = playing;
  useEffect(() => {
    setHealth(null);
    setError('');
    setLastUpdate(null);
    setBackendDemo(false);
    setCameraIds(['cam01', 'cam02']);
    if (mode === 'demo') {
      setStatus('demo');
      seconds.current = 0;
      setTracks(makeDemoTracks());
      setEvents(
        makeDemoTracks(0, new Date().toISOString()).filter(
          (t) => t.class !== 'bird',
        ),
      );
      const timer = setInterval(() => {
        if (!playingRef.current) return;
        seconds.current += 0.1;
        const now = Date.now();
        setTracks(makeDemoTracks(seconds.current, new Date(now).toISOString()));
        setLastUpdate(now);
      }, 100);
      return () => clearInterval(timer);
    }
    setTracks([]);
    setEvents([]);
    setStatus('connecting');
    let disposed = false,
      socket: WebSocket | null = null,
      retries = 0;
    let reconnect: ReturnType<typeof setTimeout>,
      poll: ReturnType<typeof setTimeout>;
    let latest = new Map<string, DisplayTrack>();
    const tombstones = new Map<string, number>();
    const controller = new AbortController();
    const receive = (item: unknown) => {
      const track = parseTrack(item);
      if (!track) return;
      const time = Date.parse(track.timestampUtc);
      // Reject expired, future-dated and out-of-order packets. Never resurrect a lost track.
      if (
        Date.now() - time > 5000 ||
        time - Date.now() > 5000 ||
        time <= (tombstones.get(track.trackId) ?? 0)
      )
        return;
      const previous = latest.get(track.trackId);
      if (previous && time < Date.parse(previous.timestampUtc)) return;
      if (track.state === 'lost') {
        latest.delete(track.trackId);
        tombstones.set(track.trackId, time);
      } else latest.set(track.trackId, mergeTrack(previous, track));
      if (tombstones.size > 2000)
        tombstones.delete(tombstones.keys().next().value!);
      setLastUpdate(Date.now());
    };
    const connect = () => {
      if (disposed) return;
      const url = new URL(`${backend}/ws/tracks`, window.location.origin);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(url);
      socket.onopen = () => {
        if (!disposed) {
          retries = 0;
          setStatus('live');
          setError('');
        }
      };
      socket.onmessage = (e) => {
        if (!disposed) {
          try {
            receive(JSON.parse(e.data));
          } catch {
            setError('已忽略格式异常的轨迹消息。');
          }
        }
      };
      socket.onclose = () => {
        if (disposed) return;
        setStatus('offline');
        setError('实时连接中断，正在重连。旧轨迹将在 5 秒后移除。');
        reconnect = setTimeout(connect, Math.min(1000 * 2 ** retries++, 15000));
      };
      socket.onerror = () => socket?.close();
    };
    const get = async (path: string) => {
      const response = await fetch(`${backend}${path}`, {
        cache: 'no-store',
        credentials: 'same-origin',
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(6000)]),
      });
      if (!response.ok)
        throw new Error(
          response.status === 401
            ? '网关需要登录，请先通过同源网关认证。'
            : `网关返回 ${response.status}，请检查服务和反向代理。`,
        );
      return response.json();
    };
    const pollServer = async () => {
      try {
        const [h, config, snapshot, log] = await Promise.all([
          get('/api/v1/health'),
          get('/api/v1/config'),
          get('/api/v1/tracks/active'),
          get('/api/v1/events?limit=100'),
        ]);
        if (disposed) return;
        const health = parseHealth(h);
        if (
          !health ||
          !isRecord(config) ||
          !isRecord(snapshot) ||
          !isRecord(log) ||
          !Array.isArray(snapshot.tracks) ||
          !Array.isArray(log.events)
        )
          throw new Error('网关数据结构不匹配。');
        setHealth(health);
        setBackendDemo(config.demoMode === true);
        if (Array.isArray(config.cameraIds))
          setCameraIds(
            config.cameraIds.filter((id: unknown) => typeof id === 'string'),
          );
        snapshot.tracks.forEach(receive);
        setEvents(
          log.events
            .map(parseTrack)
            .filter((t: Track | null): t is Track => t !== null),
        );
        if (socket?.readyState === WebSocket.OPEN) {
          setStatus('live');
          setError('');
        }
      } catch (e) {
        if (!disposed) {
          setStatus('offline');
          setHealth(null);
          setError(
            e instanceof Error
              ? e.message
              : '无法连接网关；请检查网络、CORS 与服务状态。',
          );
        }
      } finally {
        if (!disposed) poll = setTimeout(pollServer, 4000);
      }
    };
    connect();
    void pollServer();
    const paint = setInterval(() => {
      const now = Date.now();
      latest = new Map(
        [...latest].filter(([, t]) => now - Date.parse(t.timestampUtc) < 5000),
      );
      setTracks([...latest.values()]);
    }, 100);
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(reconnect);
      clearTimeout(poll);
      clearInterval(paint);
      socket?.close();
    };
  }, [mode, backend]);
  return {
    tracks,
    events,
    health,
    cameraIds,
    status,
    error,
    backendDemo,
    lastUpdate,
  };
}
