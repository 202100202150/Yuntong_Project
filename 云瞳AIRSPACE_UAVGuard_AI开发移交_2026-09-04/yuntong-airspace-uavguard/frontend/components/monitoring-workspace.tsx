'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  ArrowDownToLine,
  ArrowUpRight,
  Bell,
  Bird,
  Box,
  Camera,
  Check,
  ChevronRight,
  CircleHelp,
  Crosshair,
  Drone,
  Expand,
  Layers,
  LayoutDashboard,
  LocateFixed,
  Navigation,
  Pause,
  Plane,
  Play,
  Radar,
  Radio,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Target,
  TriangleAlert,
  Video,
  Volume2,
  X,
  type LucideIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import AirspaceView from '@/components/airspace-view';
import {
  eventKey,
  normalizeBackend,
  objectStyles,
  recordingPaths,
  speedOf,
  type ObjectClass,
  type Track,
} from '@/lib/airspace';
import { useAirspace } from '@/lib/use-airspace';

const icons: Record<ObjectClass, LucideIcon> = {
  multirotor: Drone,
  fixed_wing_uav: Plane,
  bird: Bird,
  unknown: Navigation,
};
type Modal = 'settings' | 'events' | 'devices' | 'layers' | 'help' | null;
const timeLabel = (value: string) =>
  new Date(value).toLocaleTimeString('zh-CN', {
    hour12: false,
    timeZone: 'Asia/Shanghai',
  });
const eventTitle = (t: Track) =>
  t.state === 'lost'
    ? '目标轨迹丢失'
    : t.state === 'candidate'
      ? '候选目标等待确认'
      : t.class === 'bird'
        ? '鸟类目标已识别'
        : '空中目标已确认 · 待核验';

function VideoFeed({
  id,
  title,
  enabled,
  backend,
  onOpen,
}: {
  id: string;
  title: string;
  enabled: boolean;
  backend: string;
  onOpen?: () => void;
}) {
  const [failed, setFailed] = useState(false),
    [ready, setReady] = useState(false),
    [revision, setRevision] = useState(0);
  useEffect(() => {
    setFailed(false);
    setReady(false);
  }, [enabled, id, backend]);
  return (
    <div
      className={`video-placeholder ${enabled && !failed ? 'has-video' : ''}`}
    >
      {enabled && !failed ? (
        // MJPEG is a long-lived stream; static next/image optimization cannot handle it.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={`${backend}/api/v1/cameras/${encodeURIComponent(id)}/mjpeg?view=${revision}`}
          alt={`${title}实时视频`}
          onError={() => setFailed(true)}
          onLoad={() => setReady(true)}
        />
      ) : (
        <>
          <Camera />
          <span>
            {failed
              ? '画面暂不可用'
              : enabled
                ? '等待视频帧'
                : '尚未接入真实视频'}
          </span>
          <span className="mono">{id} · RTSP → 视频网关</span>
        </>
      )}
      <div className="video-label">
        <span>{title}</span>
        <span>
          <i className={`feed-dot ${enabled && !failed ? 'online' : ''}`} />
          {enabled && !failed
            ? ready
              ? '实时流'
              : '连接中'
            : failed
              ? '异常'
              : '未接入'}
        </span>
      </div>
      {failed && (
        <Button
          variant="outline"
          size="xs"
          onClick={() => {
            setFailed(false);
            setReady(false);
            setRevision((r) => r + 1);
          }}
        >
          <RefreshCw />
          重新连接
        </Button>
      )}
      {onOpen && (
        <Button
          variant="ghost"
          size="icon-xs"
          className="feed-expand"
          aria-label={`放大${title}`}
          onClick={onOpen}
        >
          <Expand />
        </Button>
      )}
    </div>
  );
}

export default function MonitoringWorkspace() {
  const [mode, setMode] = useState<'demo' | 'live'>('demo'),
    [backend, setBackend] = useState('');
  const [playing, setPlaying] = useState(true),
    [selected, setSelected] = useState('UAV-017');
  const [modal, setModal] = useState<Modal>(null),
    [feedOpen, setFeedOpen] = useState<string | null>(null);
  const [draftMode, setDraftMode] = useState<'demo' | 'live'>('demo'),
    [draftBackend, setDraftBackend] = useState(''),
    [formError, setFormError] = useState('');
  const [ptzId, setPtzId] = useState('ptz01'),
    [draftPtz, setDraftPtz] = useState('ptz01');
  const [topDown, setTopDown] = useState(false),
    [trails, setTrails] = useState(true),
    [uncertainty, setUncertainty] = useState(true),
    [campus, setCampus] = useState(true);
  const [expanded, setExpanded] = useState(false),
    [focus, setFocus] = useState<string | null>(null),
    [focusRevision, setFocusRevision] = useState(0);
  const [filter, setFilter] = useState('all'),
    [search, setSearch] = useState(''),
    [watching, setWatching] = useState(false),
    [sound, setSound] = useState(false);
  const [clock, setClock] = useState('—:—:—'),
    [date, setDate] = useState('UTC+8'),
    [toast, setToast] = useState('');
  const [acknowledged, setAcknowledged] = useState<Set<string>>(
      () => new Set(),
    ),
    [extraEvents, setExtraEvents] = useState<Track[]>([]);
  const audio = useRef<AudioContext | null>(null),
    watchStartedAt = useRef(0),
    seen = useRef(new Set<string>()),
    primed = useRef(false);
  const data = useAirspace(mode, backend, playing);
  const tracks = mode === 'live' && data.status === 'demo' ? [] : data.tracks;
  const events = useMemo(
    () =>
      [...(mode === 'demo' ? extraEvents : []), ...data.events]
        .sort((a, b) => Date.parse(b.timestampUtc) - Date.parse(a.timestampUtc))
        .slice(0, 100),
    [mode, extraEvents, data.events],
  );
  const target = tracks.find((t) => t.trackId === selected) ?? tracks[0];
  const TargetIcon = target ? icons[target.class] : Crosshair;
  const isSimulated = mode === 'demo' || data.backendDemo;
  const isConnected =
    mode === 'live' && data.status === 'live' && data.health !== null;
  const pending = events.filter(
    (t) =>
      t.state === 'confirmed' &&
      t.class !== 'bird' &&
      !acknowledged.has(eventKey(t)),
  ).length;
  const visibleTracks = tracks.filter(
    (t) =>
      (filter === 'all' ||
        (filter === 'uav'
          ? ['multirotor', 'fixed_wing_uav'].includes(t.class)
          : t.class === filter)) &&
      `${t.trackId} ${objectStyles[t.class].label}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setClock(
        now.toLocaleTimeString('zh-CN', {
          hour12: false,
          timeZone: 'Asia/Shanghai',
        }),
      );
      setDate(now.toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' }));
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(''), 4200);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!expanded) return;
    const escape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExpanded(false);
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, [expanded]);
  useEffect(() => {
    const keys = new Set(events.map(eventKey));
    if (
      primed.current &&
      watching &&
      events.some(
        (t) =>
          !seen.current.has(eventKey(t)) &&
          Date.parse(t.timestampUtc) >= watchStartedAt.current &&
          t.state === 'confirmed' &&
          t.class !== 'bird',
      )
    ) {
      setToast('收到新的确认目标事件，请进行人工授权核验。');
      if (sound && audio.current?.state === 'running') {
        const osc = audio.current.createOscillator(),
          gain = audio.current.createGain();
        osc.connect(gain);
        gain.connect(audio.current.destination);
        osc.frequency.value = 740;
        gain.gain.setValueAtTime(0.07, audio.current.currentTime);
        gain.gain.exponentialRampToValueAtTime(
          0.001,
          audio.current.currentTime + 0.3,
        );
        osc.start();
        osc.stop(audio.current.currentTime + 0.3);
      }
    }
    seen.current = keys;
    primed.current = true;
  }, [events, watching, sound]);
  useEffect(
    () => () => {
      void audio.current?.close();
    },
    [],
  );
  const openSettings = () => {
    setDraftMode(mode);
    setDraftBackend(backend);
    setDraftPtz(ptzId);
    setFormError('');
    setModal('settings');
  };
  const focusTarget = () => {
    if (target) {
      setFocus(target.trackId);
      setFocusRevision((r) => r + 1);
      setToast(`已聚焦 ${target.trackId}，仅改变三维视角，不控制云台。`);
    }
  };
  const startWatch = () => {
    if (!watching) {
      watchStartedAt.current = Date.now();
      try {
        audio.current ??= new AudioContext();
        void audio.current
          .resume()
          .catch(() => setToast('浏览器未允许声音；视觉提示仍然可用。'));
      } catch {
        /* Visual notifications remain available. */
      }
      setToast(
        isSimulated
          ? '已开启演示值守；新确认事件将提示，未连接真实设备。'
          : '已开启本机值守提示；是否违规仍需人工核验。',
      );
    } else setToast('已结束本机值守提示，轨迹接收继续运行。');
    setWatching((v) => !v);
  };
  const exportSnapshot = () => {
    const blob = new Blob(
      [
        JSON.stringify(
          {
            exportedAt: new Date().toISOString(),
            dataSource: isSimulated ? 'simulation' : 'backend',
            coordinateSystem: 'local ENU, meters',
            tracks,
          },
          null,
          2,
        ),
      ],
      { type: 'application/json' },
    );
    const url = URL.createObjectURL(blob),
      a = document.createElement('a');
    a.href = url;
    a.download = `airspace-${isSimulated ? 'demo' : 'live'}-${Date.now()}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setToast('轨迹快照已导出，文件中包含数据来源标记。');
  };
  const stats: [string, string, string, string, LucideIcon][] = [
    [
      '活动目标',
      String(tracks.length).padStart(2, '0'),
      '个',
      '当前有效的融合轨迹',
      Target,
    ],
    [
      '确认无人机',
      String(
        tracks.filter(
          (t) =>
            t.state === 'confirmed' &&
            ['multirotor', 'fixed_wing_uav'].includes(t.class),
        ).length,
      ).padStart(2, '0'),
      '架',
      '类别识别 ≠ 违规判定',
      Drone,
    ],
    [
      '待核验事件',
      String(pending).padStart(2, '0'),
      '条',
      '最近 100 条事件 · 本机已阅',
      TriangleAlert,
    ],
    ['示例空域半径', '1.0', 'km', '界面展示范围 · 非实测能力', Radar],
    [
      '同步残差 P95',
      mode === 'demo'
        ? '12.4'
        : (data.health?.stereo?.syncResidualP95Ms?.toFixed(1) ?? '—'),
      'ms',
      mode === 'demo' ? '模拟指标 · 待现场验收' : '来自后端同步健康信息',
      Activity,
    ],
  ];
  const fixedIds = data.cameraIds.filter((id) => id !== ptzId).slice(0, 2);
  for (const fallback of ['cam01', 'cam02', 'cam03']) {
    if (fixedIds.length >= 2) break;
    if (!fixedIds.includes(fallback) && fallback !== ptzId)
      fixedIds.push(fallback);
  }
  const canShowCamera = (id: string) =>
    isConnected && data.cameraIds.includes(id);
  const feedTitle = (id: string) =>
    id === ptzId ? 'PTZ · 光学确认' : `${id.toUpperCase()} · 固定视角`;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <Radar size={23} />
          </div>
          <div>
            <div className="brand-title">
              云瞳<small>AIRSPACE</small>
            </div>
            <div className="brand-subtitle">校园低空安全监测平台</div>
          </div>
        </div>
        <div className="top-status">
          <span className="hide-medium">
            <span
              className={`status-dot ${data.status === 'offline' ? 'offline' : ''}`}
            />
            {mode === 'demo'
              ? '演示系统运行中'
              : isConnected
                ? '数据网关已连接'
                : data.status === 'connecting'
                  ? '正在连接网关'
                  : '数据网关离线'}
          </span>
          <span className="hide-small">
            {date} <span className="muted">UTC+8</span>
          </span>
          <span className="clock mono">{clock}</span>
          <Button
            size="icon"
            variant="ghost"
            className="notification-button"
            aria-label={`查看事件，${pending}条待核验`}
            onClick={() => setModal('events')}
          >
            <Bell size={17} />
            {pending > 0 && <i />}
          </Button>
          <div className="avatar" title="本机演示值守，不代表已登录学校账户">
            值守
          </div>
        </div>
      </header>
      <nav className="sidebar" aria-label="主导航">
        <Button
          variant="ghost"
          className="nav-item active"
          onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
        >
          <LayoutDashboard />
          态势
        </Button>
        <Button
          variant="ghost"
          className="nav-item"
          onClick={() =>
            document
              .getElementById('video-section')
              ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
          }
        >
          <Video />
          视频
        </Button>
        <Button
          variant="ghost"
          className="nav-item"
          onClick={() => setModal('events')}
        >
          <Bell />
          事件
        </Button>
        <Button
          variant="ghost"
          className="nav-item"
          onClick={() => setModal('devices')}
        >
          <Radio />
          设备
        </Button>
        <div className="nav-bottom">
          <Button
            variant="ghost"
            className="nav-item"
            onClick={() => setModal('help')}
          >
            <CircleHelp />
            说明
          </Button>
          <Button variant="ghost" className="nav-item" onClick={openSettings}>
            <Settings2 />
            设置
          </Button>
        </div>
      </nav>
      <main className="workspace">
        <div className="heading-row">
          <div>
            <div className="eyebrow">SITUATIONAL AWARENESS / 实时监控</div>
            <h1>校园空域态势总览</h1>
          </div>
          <div className="heading-actions">
            <span className={`demo-badge ${!isSimulated ? 'live-badge' : ''}`}>
              {mode === 'demo'
                ? 'DEMO · 模拟数据'
                : data.backendDemo
                  ? 'DEMO · 后端模拟'
                  : isConnected
                    ? 'LIVE · 网关数据'
                    : 'LIVE · 等待接入'}
            </span>
            <Button
              variant="outline"
              className="optional"
              onClick={openSettings}
            >
              <Settings2 />
              接入配置
            </Button>
            <Button
              variant={watching ? 'outline' : 'default'}
              onClick={startWatch}
            >
              <ShieldCheck />
              {watching ? '值守中 · 结束' : '开始值守'}
            </Button>
          </div>
        </div>
        {mode === 'live' && data.error && (
          <output className="connection-banner">
            <TriangleAlert size={15} />
            <span>{data.error} 未接收到真实数据时不显示模拟目标。</span>
            <Button variant="ghost" size="xs" onClick={openSettings}>
              检查配置
            </Button>
          </output>
        )}
        <section className="metrics" aria-label="空域概览">
          {stats.map(([label, value, unit, caption, Icon], i) => (
            <div className="metric" key={label}>
              <div className="metric-label">
                {label}
                <Icon />
              </div>
              <div
                className={`metric-value mono ${i === 1 ? 'lime' : i === 2 ? 'orange' : ''}`}
              >
                {value}
                <small>{unit}</small>
              </div>
              <div className="metric-caption">{caption}</div>
            </div>
          ))}
        </section>
        <div className="main-grid">
          <section
            className={`panel map-panel ${expanded ? 'map-expanded' : ''}`}
          >
            <div className="panel-header">
              <div className="panel-title">
                <Box />
                三维空域 <small>LIVE AIRSPACE</small>
              </div>
              <div className="panel-tools">
                <div className="view-toggle">
                  <Button
                    size="xs"
                    variant={!topDown ? 'secondary' : 'ghost'}
                    aria-pressed={!topDown}
                    onClick={() => setTopDown(false)}
                  >
                    3D
                  </Button>
                  <Button
                    size="xs"
                    variant={topDown ? 'secondary' : 'ghost'}
                    aria-pressed={topDown}
                    onClick={() => setTopDown(true)}
                  >
                    2D
                  </Button>
                </div>
                <Button
                  size="icon-xs"
                  variant="ghost"
                  aria-label="设置空域图层"
                  onClick={() => setModal('layers')}
                >
                  <Layers />
                </Button>
                <Button
                  size="icon-xs"
                  variant="ghost"
                  aria-label={expanded ? '收起空域' : '展开空域'}
                  onClick={() => setExpanded((v) => !v)}
                >
                  {expanded ? <X /> : <Expand />}
                </Button>
              </div>
            </div>
            <AirspaceView
              tracks={tracks}
              selected={target?.trackId ?? ''}
              onSelect={setSelected}
              topDown={topDown}
              trails={trails}
              uncertainty={uncertainty}
              campus={campus && mode === 'demo'}
              focus={focus}
              focusRevision={focusRevision}
              demo={mode === 'demo'}
            />
            <div className="map-footer">
              <span>
                <span
                  className={`status-dot ${data.status === 'offline' ? 'offline' : ''}`}
                />
                {mode === 'demo'
                  ? playing
                    ? '模拟轨迹更新中 · 10 Hz'
                    : '模拟轨迹已暂停'
                  : isConnected
                    ? '网关连接正常'
                    : '等待实时连接'}
                <span className="map-hint"> · 拖动旋转 / 滚轮缩放</span>
              </span>
              <div className="panel-tools">
                {mode === 'demo' && (
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label={playing ? '暂停模拟轨迹' : '继续模拟轨迹'}
                    onClick={() => setPlaying((p) => !p)}
                  >
                    {playing ? <Pause /> : <Play />}
                  </Button>
                )}
                <span className="mono">ENU · m</span>
              </div>
            </div>
          </section>
          <aside className="panel detail-panel">
            <div className="panel-header">
              <div className="panel-title">
                <Crosshair />
                目标详情
              </div>
              <Badge variant="outline">
                {target
                  ? target.state === 'confirmed'
                    ? '轨迹已确认'
                    : '候选轨迹'
                  : '等待目标'}
              </Badge>
            </div>
            {target ? (
              <>
                <div className="detail-id">
                  <div>
                    <strong className="mono">{target.trackId}</strong>
                    <p>
                      {target.cameraIds.join(' + ')} · 融合跟踪
                      {isSimulated ? ' / 示例' : ''}
                    </p>
                  </div>
                  <TargetIcon
                    size={26}
                    color={objectStyles[target.class].color}
                  />
                </div>
                <div className="target-optic">
                  {canShowCamera(ptzId) ? (
                    <VideoFeed
                      id={ptzId}
                      title="PTZ · 光学确认"
                      backend={backend}
                      enabled
                      onOpen={() => setFeedOpen(ptzId)}
                    />
                  ) : (
                    <>
                      <span className="optic-corner">
                        PTZ / OPTICAL CONFIRMATION
                      </span>
                      <TargetIcon />
                      <small>云台确认画面 · 待接入</small>
                    </>
                  )}
                </div>
                <div className="detail-meta">
                  <div className="detail-row">
                    <span>识别类别</span>
                    <span style={{ color: objectStyles[target.class].color }}>
                      {objectStyles[target.class].label}
                    </span>
                  </div>
                  <div className="detail-row">
                    <span>分类置信度</span>
                    <span className="mono">
                      {(target.classConfidence * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="confidence-bar">
                    <span
                      style={{
                        width: `${target.classConfidence * 100}%`,
                        background: objectStyles[target.class].color,
                      }}
                    />
                  </div>
                  <div className="detail-row">
                    <span>实时三维坐标</span>
                    <span className="muted">ENU / 米</span>
                  </div>
                  <div className="enu-grid">
                    {['E 东向', 'N 北向', 'U 高度'].map((l, i) => (
                      <div key={l}>
                        <small>{l}</small>
                        <strong className="mono">
                          {target.positionEnuM[i].toFixed(1)}
                        </strong>
                      </div>
                    ))}
                  </div>
                  <div className="detail-row">
                    <span>飞行速度 / 重投影</span>
                    <span className="mono">
                      {speedOf(target).toFixed(1)} m/s{' '}
                      <span className="muted">
                        / {target.reprojectionErrorPx.toFixed(2)} px
                      </span>
                    </span>
                  </div>
                  <div className="detail-row">
                    <span>坐标标准差 (E/N/U)</span>
                    <span className="mono">
                      ±{' '}
                      {target.positionStdM.map((n) => n.toFixed(1)).join(' / ')}{' '}
                      m
                    </span>
                  </div>
                  <div className="detail-row">
                    <span>授权状态</span>
                    <span className="orange">未核验 · 不自动判定黑飞</span>
                  </div>
                  <div className="detail-row timestamp-row">
                    <span>观测时间 UTC+8</span>
                    <span className="mono">
                      {timeLabel(target.timestampUtc)}
                    </span>
                  </div>
                </div>
                <div className="detail-actions">
                  <Button variant="outline" onClick={focusTarget}>
                    <LocateFixed />
                    聚焦此目标
                    <ArrowUpRight />
                  </Button>
                </div>
              </>
            ) : (
              <div className="empty-detail">
                <Crosshair />
                <p>等待有效空中目标</p>
                <small>选中轨迹后显示类别、坐标与误差</small>
              </div>
            )}
          </aside>
        </div>

        <div className="lower-grid">
          <section className="panel" id="video-section">
            <div className="panel-header">
              <div className="panel-title">
                <Video />
                多源视觉监控 <small>CAMERA FEEDS</small>
              </div>
              <Button
                size="xs"
                variant="ghost"
                onClick={() => setModal('devices')}
              >
                <Radio />
                {mode === 'demo'
                  ? '设备待接入'
                  : `${data.health?.cameras.filter((c) => c.status === 'online').length ?? 0} 在线`}
                <ChevronRight />
              </Button>
            </div>
            <div className="video-grid">
              {fixedIds.map((id, i) => (
                <VideoFeed
                  key={`${i}-${id}`}
                  id={id}
                  title={`${id.toUpperCase()} · ${i === 0 ? '西侧' : '东侧'}固定视角`}
                  enabled={canShowCamera(id)}
                  backend={backend}
                  onOpen={() => setFeedOpen(id)}
                />
              ))}
            </div>
          </section>
          <section className="panel">
            <div className="panel-header">
              <div className="panel-title">
                <Bell />
                近期事件
              </div>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => setModal('events')}
              >
                查看全部
                <ChevronRight />
              </Button>
            </div>
            {events.length ? (
              events.slice(0, 3).map((t) => (
                <button
                  key={eventKey(t)}
                  className="event-row event-button"
                  onClick={() => {
                    setSelected(t.trackId);
                    setModal('events');
                  }}
                >
                  {acknowledged.has(eventKey(t)) ? (
                    <Check />
                  ) : (
                    <TriangleAlert />
                  )}
                  <div className="event-copy">
                    <strong>{eventTitle(t)}</strong>
                    <p>
                      {t.trackId} · {objectStyles[t.class].label}
                      {isSimulated ? ' / 模拟' : ''}
                    </p>
                  </div>
                  <time className="mono">
                    {timeLabel(t.timestampUtc).slice(0, 5)}
                  </time>
                </button>
              ))
            ) : (
              <div className="empty-events">
                暂无事件记录
                <br />
                <small>候选目标不会触发确认告警</small>
              </div>
            )}
          </section>
        </div>
        <section className="panel targets-panel">
          <div className="panel-header">
            <div className="panel-title">
              <Target />
              活动目标 <small>TRACKING OBJECTS</small>
            </div>
            <div className="panel-tools">
              <div className="target-search">
                <Search size={12} />
                <Input
                  aria-label="搜索目标编号或类别"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="搜索目标"
                />
              </div>
              <Button
                size="icon-xs"
                variant="ghost"
                aria-label="导出轨迹快照"
                onClick={exportSnapshot}
              >
                <ArrowDownToLine />
              </Button>
            </div>
          </div>
          <div className="target-filters">
            {[
              ['all', '全部'],
              ['uav', '无人机'],
              ['bird', '鸟类'],
              ['unknown', '未知'],
            ].map(([value, label]) => (
              <Button
                key={value}
                variant={filter === value ? 'secondary' : 'ghost'}
                size="xs"
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {label}
              </Button>
            ))}
            <span>{visibleTracks.length} 个目标</span>
          </div>
          <div className="target-list">
            {visibleTracks.map((t) => {
              const Icon = icons[t.class];
              return (
                <Button
                  key={t.trackId}
                  variant="ghost"
                  className={`target-chip ${target?.trackId === t.trackId ? 'selected' : ''}`}
                  aria-pressed={target?.trackId === t.trackId}
                  onClick={() => setSelected(t.trackId)}
                >
                  <Icon style={{ color: objectStyles[t.class].color }} />
                  <span className="target-chip-text">
                    <strong className="mono">{t.trackId}</strong>
                    <small>
                      {objectStyles[t.class].short} · H{' '}
                      {t.positionEnuM[2].toFixed(0)}m
                    </small>
                  </span>
                  <span
                    className="mono text-[11px]"
                    style={{ color: objectStyles[t.class].color }}
                  >
                    {(t.classConfidence * 100).toFixed(1)}%
                  </span>
                </Button>
              );
            })}
          </div>
          {!visibleTracks.length && (
            <div className="empty-events">没有匹配的活动目标</div>
          )}
        </section>
        <footer className="footer">
          <span>云瞳 AIRSPACE · 仅监测与人工核验，不提供反制功能</span>
          <span>
            {isSimulated
              ? '模拟数据演示 / 校园模型为示意'
              : '网关数据 / 请以校准和验收结果为准'}
          </span>
        </footer>
      </main>

      <Dialog
        open={modal !== null}
        onOpenChange={(open) => {
          if (!open) setModal(null);
        }}
      >
        <DialogContent
          className={`dashboard-dialog ${modal === 'events' ? 'wide-dialog' : ''}`}
        >
          <DialogHeader>
            <DialogTitle>
              {
                {
                  settings: '数据源与接入配置',
                  events: '事件中心',
                  devices: '设备与链路状态',
                  layers: '空域显示图层',
                  help: '关于这一版监控工作台',
                }[modal ?? 'help']
              }
            </DialogTitle>
            <DialogDescription>
              {modal === 'settings'
                ? '前端不保存摄像头密码；真实设备通过校园内网视频与坐标网关接入。'
                : modal === 'events'
                  ? '确认只代表形成稳定轨迹，不代表违规。已阅标记仅在当前页面会话有效。'
                  : modal === 'devices'
                    ? '以网关上报的状态为准；模拟模式不会虚构摄像头在线状态。'
                    : modal === 'layers'
                      ? '调整三维空域的可视信息，不改变识别算法与告警规则。'
                      : '当前版本用于界面演示与接口联调，不能代替真实探测、标定和现场验收。'}
            </DialogDescription>
          </DialogHeader>
          {modal === 'settings' && (
            <form
              className="settings-form"
              onSubmit={(e) => {
                e.preventDefault();
                try {
                  const resolved = normalizeBackend(
                    draftBackend,
                    window.location.protocol,
                  );
                  if (!draftPtz.trim() || draftPtz.length > 64)
                    throw new Error('请填写有效的云台相机 ID。');
                  setBackend(resolved);
                  setMode(draftMode);
                  setPtzId(draftPtz.trim());
                  setWatching(false);
                  setExtraEvents([]);
                  setAcknowledged(new Set());
                  primed.current = false;
                  setSelected(draftMode === 'demo' ? 'UAV-017' : '');
                  setModal(null);
                  setToast(
                    draftMode === 'demo'
                      ? '已切换到模拟演示。'
                      : '已切换到网关数据；连接失败时不会回退到模拟数据。',
                  );
                } catch (err) {
                  setFormError(
                    err instanceof Error ? err.message : '地址格式不正确。',
                  );
                }
              }}
            >
              <div className="source-options">
                <Button
                  type="button"
                  variant={draftMode === 'demo' ? 'secondary' : 'outline'}
                  aria-pressed={draftMode === 'demo'}
                  onClick={() => setDraftMode('demo')}
                >
                  <Play />
                  模拟演示
                </Button>
                <Button
                  type="button"
                  variant={draftMode === 'live' ? 'secondary' : 'outline'}
                  aria-pressed={draftMode === 'live'}
                  onClick={() => setDraftMode('live')}
                >
                  <Radio />
                  实时网关
                </Button>
              </div>
              <label htmlFor="backend-url">
                网关基础地址 <small>留空使用同源 /api 和 /ws</small>
              </label>
              <Input
                id="backend-url"
                value={draftBackend}
                onChange={(e) => setDraftBackend(e.target.value)}
                placeholder="https://gateway.campus.example"
                autoComplete="off"
                spellCheck={false}
              />
              <label htmlFor="ptz-id">云台确认视频相机 ID</label>
              <Input
                id="ptz-id"
                value={draftPtz}
                onChange={(e) => setDraftPtz(e.target.value)}
                placeholder="ptz01"
              />
              <div className="info-box">
                推荐校园内网同源部署。线上演示地址不会代理访问学校内网。跨域网关须自行配置
                HTTPS、访问控制与 CORS。不要填写 RTSP 密码链接。
              </div>
              {formError && (
                <p className="orange" role="alert">
                  {formError}
                </p>
              )}
              <Button type="submit">
                应用配置
                <ArrowUpRight />
              </Button>
            </form>
          )}
          {modal === 'layers' && (
            <div className="settings-form">
              {[
                ['轨迹尾迹', trails, setTrails],
                ['坐标不确定性（投影示意）', uncertainty, setUncertainty],
                ['示意校园模型（仅模拟模式）', campus, setCampus],
              ].map(([label, value, setter]) => (
                <div className="switch-row" key={String(label)}>
                  <span>{String(label)}</span>
                  <Switch
                    aria-label={String(label)}
                    checked={value as boolean}
                    onCheckedChange={setter as (v: boolean) => void}
                  />
                </div>
              ))}
              <p className="muted text-xs">
                误差显示使用后端 E/N/U
                标准差，并非完整协方差置信椭球。真实模式不加载示意校园与相机位置。
              </p>
            </div>
          )}
          {modal === 'devices' && (
            <div className="device-list">
              {[...fixedIds, ptzId].map((id, i) => {
                const h = data.health?.cameras.find((c) => c.cameraId === id);
                return (
                  <div key={`${id}-${i}`} className="device-card">
                    <Camera />
                    <div>
                      <strong>{feedTitle(id)}</strong>
                      <p>
                        {i < 2
                          ? '固定相机 · 海康威视 · 双目定位'
                          : '变焦云台 · 光学确认 · 需新增视频通道'}
                      </p>
                    </div>
                    <span className={h?.status === 'online' ? 'lime' : 'muted'}>
                      {mode === 'demo'
                        ? '未连接'
                        : h?.status === 'online'
                          ? `${h.averageFps?.toFixed(1) ?? '—'} fps`
                          : (h?.status ?? '未配置')}
                    </span>
                  </div>
                );
              })}
              <div className="device-card">
                <Activity />
                <div>
                  <strong>分类模型</strong>
                  <p>模型训练与现场验证须在后端完成</p>
                </div>
                <span className="muted">
                  {mode === 'demo'
                    ? '模拟类别'
                    : data.health?.classifierReady
                      ? '已加载'
                      : '未就绪'}
                </span>
              </div>
              <Button variant="outline" onClick={openSettings}>
                <Settings2 />
                配置数据源
              </Button>
            </div>
          )}
          {modal === 'events' && (
            <div className="events-content">
              <div className="event-toolbar">
                <span>
                  {events.length} 条记录 / {pending} 条待核验
                </span>
                <div className="panel-tools">
                  <Volume2 size={14} />
                  <Switch
                    aria-label="新确认事件声音提示"
                    checked={sound}
                    onCheckedChange={setSound}
                  />
                  <span>声音</span>
                </div>
              </div>
              <div className="event-log">
                {events.map((t) => (
                  <article key={eventKey(t)} className="event-log-item">
                    <div className="event-log-heading">
                      <Badge
                        variant={
                          t.state === 'confirmed' ? 'secondary' : 'outline'
                        }
                      >
                        {t.state === 'confirmed'
                          ? '已确认'
                          : t.state === 'candidate'
                            ? '候选'
                            : '丢失'}
                      </Badge>
                      <strong>
                        {t.trackId} · {objectStyles[t.class].label}
                      </strong>
                      <time className="mono">{timeLabel(t.timestampUtc)}</time>
                    </div>
                    <p>
                      {eventTitle(t)} · ENU [
                      {t.positionEnuM.map((n) => n.toFixed(1)).join(', ')}] m
                    </p>
                    <div className="event-log-actions">
                      <Button
                        size="xs"
                        variant="outline"
                        onClick={() => {
                          setSelected(t.trackId);
                          setModal(null);
                          window.scrollTo({ top: 0, behavior: 'smooth' });
                          if (!tracks.some((x) => x.trackId === t.trackId))
                            setToast(
                              '该历史目标已不在活动轨迹中，事件坐标保留在记录内。',
                            );
                        }}
                      >
                        查看活动轨迹
                      </Button>
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() =>
                          setAcknowledged(
                            (prev) => new Set([...prev, eventKey(t)]),
                          )
                        }
                        disabled={acknowledged.has(eventKey(t))}
                      >
                        {acknowledged.has(eventKey(t)) ? (
                          <>
                            <Check />
                            已阅（本机）
                          </>
                        ) : (
                          '标为已阅'
                        )}
                      </Button>
                      {recordingPaths(t).map((path) => (
                        <a
                          key={path}
                          className="recording-link"
                          href={`${backend}/api/v1/recordings/${path.split('/').map(encodeURIComponent).join('/')}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          事件录像 ↗
                        </a>
                      ))}
                      {!recordingPaths(t).length && (
                        <small className="muted">暂无关联录像</small>
                      )}
                    </div>
                  </article>
                ))}
              </div>
              {!events.length && (
                <div className="empty-events">暂无事件，等待网关数据。</div>
              )}
              {mode === 'demo' && (
                <Button
                  variant="outline"
                  onClick={() => {
                    const t = tracks.find((t) => t.class === 'multirotor');
                    if (t)
                      setExtraEvents((prev) =>
                        [
                          {
                            ...t,
                            timestampUtc: new Date().toISOString(),
                            state: 'confirmed' as const,
                          },
                          ...prev,
                        ].slice(0, 30),
                      );
                  }}
                >
                  <PlusEvent />
                  生成一条演示事件
                </Button>
              )}
            </div>
          )}
          {modal === 'help' && (
            <div className="help-content">
              <p>
                用固定摄像头发现与定位目标，再由变焦云台进行光学复核。云台指向控制、识别模型与定位计算由后端负责，这一版不向硬件发送控制命令。
              </p>
              <ol>
                <li>在三维空域拖动旋转、滚轮缩放，或用键盘方向键操作。</li>
                <li>
                  点击空中目标或底部目标卡片，查看 ENU 坐标、速度与标准差。
                </li>
                <li>在“接入配置”切换到实时网关，接收现有轨迹和视频接口。</li>
              </ol>
              <div className="info-box">
                目前的 1 km
                是示例显示范围，不是相机识别能力承诺。尚未导入学校真实地图、相机标定参数或真实模型权重。
              </div>
              <p className="muted">
                平台不凭视觉类别判断是否“黑飞”。必须结合飞行授权、人工复核和学校规定。
              </p>
            </div>
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={feedOpen !== null}
        onOpenChange={(open) => {
          if (!open) setFeedOpen(null);
        }}
      >
        <DialogContent className="dashboard-dialog video-dialog">
          <DialogHeader>
            <DialogTitle>
              {feedOpen ? feedTitle(feedOpen) : '视频监控'}
            </DialogTitle>
            <DialogDescription>
              浏览器通过视频网关读取画面；不直接连接 RTSP。
            </DialogDescription>
          </DialogHeader>
          {feedOpen && (
            <VideoFeed
              id={feedOpen}
              title={feedTitle(feedOpen)}
              enabled={canShowCamera(feedOpen)}
              backend={backend}
            />
          )}
        </DialogContent>
      </Dialog>
      {toast && (
        <output className="toast-message">
          <Check size={16} />
          <span>{toast}</span>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="关闭提示"
            onClick={() => setToast('')}
          >
            <X />
          </Button>
        </output>
      )}
    </div>
  );
}
function PlusEvent() {
  return <Bell size={14} />;
}
