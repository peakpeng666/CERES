import { useEffect, useMemo, useRef, useState } from 'react';
import { useAgent } from '../agent/store';
import type { AgentSession } from '../agent/api';
import { lab } from '../lab/api';
import { studio } from '../studio/api';
import { useStudio } from '../studio/store';
import { Icon } from './Icon';
import { openFrames, openRoot, saveFile, sessionExportHref } from './nav';
import { cx } from './format';
import { useMenu } from './ui';
import s from './desk.module.css';

interface Command { name: string; desc: string; when?: (session: AgentSession | null) => boolean; run: () => void }

function useCommands(session: AgentSession | null): Command[] {
  const control = useAgent((x) => x.control);
  return useMemo(() => {
    const state = session?.state;
    const hasSession = (x: AgentSession | null) => !!x;
    return [
      { name: '确认', desc: '确认任务合同并开始执行', when: () => state === 'awaiting_confirmation', run: () => void control('confirm') },
      { name: '批准', desc: '批准当前请求', when: () => state === 'awaiting_approval', run: () => void control('approve') },
      { name: '拒绝', desc: '拒绝当前请求', when: () => state === 'awaiting_approval', run: () => void control('reject') },
      { name: '暂停', desc: '当前工具调用结束后暂停', when: () => state === 'running', run: () => void control('pause') },
      { name: '取消', desc: '放弃进行中的调用，已有记录保留', when: () => state === 'running', run: () => void control('cancel') },
      { name: '继续', desc: '从保存的阶段继续', when: () => ['paused', 'failed', 'cancelled', 'interrupted'].includes(state ?? ''), run: () => void control('resume') },
      { name: '重试', desc: '重新执行当前阶段', when: () => ['failed', 'cancelled', 'interrupted'].includes(state ?? ''), run: () => void control('retry') },
      { name: '地图', desc: '打开任务地图', when: () => !!session?.scenario_id, run: () => openRoot('map') },
      { name: '方案', desc: '打开方案列表', when: hasSession, run: () => openRoot('plans') },
      { name: '合同', desc: '打开任务合同', when: hasSession, run: () => openRoot('contract') },
      { name: '记录', desc: '打开运行记录', when: hasSession, run: () => openRoot('trace') },
      { name: '仿真', desc: '打开仿真面板', when: hasSession, run: () => openRoot('sim') },
      { name: '算法库', desc: '打开算法库', run: () => { location.hash = '#/algorithms'; } },
      { name: '物理预演', desc: '打开 MuJoCo 记录与双引擎对比', when: hasSession, run: () => openFrames([{ t: 'sim' }, { t: 'panel', id: 'physics' }]) },
      { name: '导出', desc: '导出会话实验包', when: hasSession, run: () => saveFile(sessionExportHref(session!.session_id), `${session!.session_id}.json`) },
      { name: '规划', desc: '在规划工作台打开任务地图', when: () => !!session?.scenario_id, run: () => { location.hash = `#/plan/${encodeURIComponent(session!.scenario_id!)}`; } },
      { name: '新任务', desc: '开始一个新任务', run: () => { location.hash = '#/agent'; } },
    ];
  }, [session, control]);
}

export function Composer({ session, big = false, compact = false, seed, placeholder: hint }: { session: AgentSession | null; big?: boolean; compact?: boolean; seed?: { text: string; token: number } | null; placeholder?: string }) {
  const [text, setText] = useState('');
  const [active, setActive] = useState(0);
  const [over, setOver] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const send = useAgent((x) => x.send);
  const start = useAgent((x) => x.start);
  const control = useAgent((x) => x.control);
  const sending = useAgent((x) => x.sending);
  const pending = useAgent((x) => x.pendingAction !== null);
  const sessionId = useAgent((x) => x.sessionId);
  const providers = useAgent((x) => x.providers);
  const mode = useAgent((x) => x.newMode);
  const setMode = useAgent((x) => x.setNewMode);
  const chosen = useAgent((x) => x.newProvider);
  const setChosen = useAgent((x) => x.setNewProvider);
  const auto = useAgent((x) => x.newAutoConfirm);
  const setAuto = useAgent((x) => x.setNewAutoConfirm);
  const loadProviders = useAgent((x) => x.loadProviders);
  const newMap = useAgent((x) => x.newMap);
  const setNewMap = useAgent((x) => x.setNewMap);
  const notify = useStudio((x) => x.notify);
  const commands = useCommands(session);
  const menu = useMenu();
  useEffect(() => { if (!providers) void loadProviders(); }, [providers, loadProviders]);
  useEffect(() => { if (seed) { setText(seed.text); area.current?.focus(); } }, [seed]);
  useEffect(() => { setText(''); }, [sessionId]);
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  const running = session?.state === 'running';
  const blocked = running || pending;
  const placeholder = hint ?? (!session ? (newMap ? '想在这张地图上做什么？例如：比较 3 个算法，目标是尽快完成' : '场地大小、车辆数、任务点数、容量与目标，写一句话就行')
    : session.state === 'awaiting_user' ? '回答上面的问题，或补充其他要求'
      : session.state === 'awaiting_confirmation' ? '输入“确认”开始执行，或说明要修改的地方'
        : session.state === 'awaiting_approval' ? '输入“批准”或“拒绝”，也可提出新的要求'
          : running ? '执行中，可以按右侧按钮暂停'
            : '继续修改要求，例如：改成 4 台车，只允许单趟往返。输入 / 查看命令');

  const slash = text.startsWith('/') && !text.includes(' ') ? text.slice(1) : null;
  const matches = slash === null ? [] : commands.filter((c) => (!c.when || c.when(session)) && c.name.includes(slash));
  useEffect(() => { setActive(0); }, [slash]);

  const runCommand = (c: Command) => { setText(''); c.run(); };
  const submit = () => {
    if (slash !== null && matches[active]) { runCommand(matches[active]); return; }
    const t = text.trim();
    if (!t || blocked || sending) return;
    void (session ? send(t) : start(t)).then((ok) => { if (ok) setText(''); });
  };

  const attach = async (anchor: HTMLElement) => {
    const { scenarios } = await lab.scenarios();
    const mine = scenarios.filter((x) => x.problem_class === 'fleet_routing' && (x as { origin?: string }).origin === 'user').reverse().slice(0, 8);
    menu.open(anchor, [
      ...mine.map((x) => ({ label: x.title, sub: x.summary, onSelect: () => setNewMap({ id: x.id, title: x.title }) })),
      { label: '-' },
      { label: '浏览全部地图', onSelect: () => { location.hash = '#/maps'; } },
      { label: '导入 GeoJSON 或场景', sub: '也可以把文件拖到这里', onSelect: () => file.current?.click() },
      { label: '从真实田块生成', sub: 'Fields2Benchmark', onSelect: () => { location.hash = '#/library/fields'; } },
    ]);
  };
  const importFile = async (f: File) => {
    try {
      const obj = JSON.parse(await f.text());
      const sc = obj.type === 'FeatureCollection' || obj.type === 'Feature' ? await studio.importGeojson(obj, f.name.replace(/\.(geo)?json$/i, '')) : await studio.importScenario(obj);
      setNewMap({ id: sc.id, title: sc.title });
    } catch (e) {
      notify('error', `导入失败：${(e as Error).message}`);
    }
  };

  const list = providers?.providers ?? [];
  const current = list.find((p) => p.id === (chosen ?? providers?.default));
  const modelLabel = mode === 'rule' ? '规则流程' : current ? `${current.model}${current.local ? ' · 本地' : ''}` : '读取模型…';
  const ready = current && current.enabled && (current.local ? current.status.reachable && current.status.model_present : current.has_credential);
  const pickModel = (anchor: HTMLElement) => menu.open(anchor, [
    ...list.map((p) => ({
      label: p.label, disabled: !p.enabled, on: mode !== 'rule' && p.id === (chosen ?? providers?.default),
      sub: !p.enabled ? p.status.detail : p.local ? (p.status.reachable ? '本机运行，就绪' : `未就绪：${p.status.detail}`) : '可选，额度受端点条款限制',
      onSelect: () => { setChosen(p.id); setMode('auto'); },
    })),
    { label: '规则流程', sub: '不调用大模型，作为对照基线', on: mode === 'rule', onSelect: () => setMode('rule') },
  ]);
  const pickConfirm = (anchor: HTMLElement) => menu.open(anchor, [
    { label: '手动确认合同', sub: '先看合同，确认后才执行', on: !auto, onSelect: () => setAuto(false) },
    { label: '自动确认合同', sub: '任务修改与仿真仍需批准', on: auto, onSelect: () => setAuto(true) },
  ]);

  return (
    <div className={s.composer} data-over={over || undefined}
      onDragOver={(e) => { if (!session && e.dataTransfer.types.includes('Files')) { e.preventDefault(); setOver(true); } }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { setOver(false); const f = e.dataTransfer.files[0]; if (!session && f) { e.preventDefault(); void importFile(f); } }}>
      <input ref={file} type="file" accept=".json,.geojson,application/json" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void importFile(f); e.target.value = ''; }} />
      {!session && newMap && !compact && <div className={s.attach}><Icon name="map" size={13} /><span>{newMap.title}</span><button type="button" title="移除" aria-label="移除地图" onClick={() => setNewMap(null)}><Icon name="close" size={11} /></button></div>}
      {slash !== null && matches.length > 0 && (
        <div className={s.slash} role="listbox">
          {matches.map((c, i) => <button key={c.name} type="button" role="option" aria-selected={i === active} data-active={i === active || undefined} onMouseDown={(e) => { e.preventDefault(); runCommand(c); }}>/{c.name}<small>{c.desc}</small></button>)}
        </div>
      )}
      <textarea ref={area} value={text} rows={big ? 3 : compact ? 1 : 2} placeholder={placeholder} aria-label="给智能体的消息" disabled={blocked && !running}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (slash !== null && matches.length) {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive((active + 1) % matches.length); return; }
            if (e.key === 'ArrowUp') { e.preventDefault(); setActive((active - 1 + matches.length) % matches.length); return; }
            if (e.key === 'Escape') { setText(''); return; }
          }
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
        }} />
      <div className={s.ctl}>
        {!session && !compact && <button type="button" className={s.pick} title="选择地图" aria-label="选择地图" onClick={(e) => void attach(e.currentTarget)}><Icon name="plus" size={14} /></button>}
        {session
          ? <span className={s.ro}>{session.auto_confirm ? '自动确认合同' : '手动确认合同'}</span>
          : <button type="button" className={s.pick} title="合同确认方式" onClick={(e) => pickConfirm(e.currentTarget)}>{auto ? '自动确认合同' : '手动确认合同'}<Icon name="down" size={11} /></button>}
        <span className={s.grow} />
        {session
          ? <span className={s.ro} title={session.provider?.label}>{session.provider ? `${session.provider.model}${session.provider.local ? ' · 本地' : ''}` : '规则流程'}</span>
          : <button type="button" className={s.pick} title={mode === 'rule' ? '不调用大模型' : ready ? current?.label : '模型未就绪时新任务使用规则流程，并在会话中注明'} onClick={(e) => pickModel(e.currentTarget)}>{modelLabel}{mode !== 'rule' && !ready && providers ? ' · 未就绪' : ''}<Icon name="down" size={11} /></button>}
        {running
          ? <button type="button" className={cx(s.send)} data-stop title="暂停（当前工具调用结束后）" aria-label="暂停" disabled={pending} onClick={() => void control('pause').catch((e) => notify('error', String(e)))}><Icon name="stop" size={13} /></button>
          : <button type="button" className={s.send} title="发送（Enter）" aria-label="发送" disabled={!text.trim() || blocked || sending} onClick={submit}><Icon name="up" size={15} /></button>}
      </div>
      {menu.node}
    </div>
  );
}

const EXAMPLES = [
  '在 200×120 米的果园里，3 台机器人从西南角车库出发，给 40 个点采样，每台最多装 10 个样本，避开两个池塘和一栋房子',
  '5 公顷麦田分成 6 块，4 辆无人车巡检 60 个巡检点，要求尽快完成，有 3 棵大树和一条水渠',
  '30 亩菜地，两台小车从东北角出发运输，每台载重 20，十二个投放点聚集分布，每个点要在上午九点前送达',
];

export function NewTask() {
  const [seed, setSeed] = useState<{ text: string; token: number } | null>(null);
  const actionError = useAgent((x) => x.actionError);
  return (
    <div className={s.hello}>
      <div className={s.helloIn}>
        <h1>今天要安排哪块地？</h1>
        {actionError && <p className={s.actionError}>{actionError}</p>}
        <Composer session={null} big seed={seed} />
        <ul className={s.helloList}>
          {EXAMPLES.map((t) => <li key={t}><button type="button" onClick={() => setSeed({ text: t, token: Date.now() })}>{t}</button></li>)}
        </ul>
      </div>
    </div>
  );
}
