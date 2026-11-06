import { useMemo, useRef } from 'react';
import type { Route } from '../app/router';
import { themeReveal } from '../lib/motion';
import { PanelHost, type PanelId } from '../studio/panelRegistry';
import { useDesk, type Motion, type Theme } from './store';
import { Marker, TopBar } from './ui';
import s from './desk.module.css';

const SECTIONS: { id: string; label: string; panel?: PanelId }[] = [
  { id: 'general', label: '通用' },
  { id: 'models', label: '模型与用量', panel: 'models' },
  { id: 'capabilities', label: '能力', panel: 'capabilities' },
  { id: 'data', label: '数据', panel: 'data' },
  { id: 'tools', label: '工具与闸门', panel: 'tools' },
  { id: 'evals', label: '智能体评测', panel: 'evals' },
  { id: 'services', label: '服务', panel: 'services' },
  { id: 'logs', label: '日志', panel: 'console' },
  ...(window.desktop ? [{ id: 'desktop', label: '桌面应用', panel: 'desktop' as PanelId }] : []),
];

const KEYS: [string, string][] = [['⌘K', '搜索任务、地图、面板与操作'], ['⌘N', '新任务'], ['⌘B', '任务栏'], ['⌘J', '右侧面板'], ['Esc', '退回上一层'], ['Enter', '发送，Shift+Enter 换行'], ['/', '在输入框里调出命令'], ['⌘Enter', '在规划页求解并对比推荐的算法'], ['⌘Z / ⇧⌘Z', '撤销 / 重做'], ['⌘S', '保存地图'], ['V H T O P D U C', '地图工具：选择、平移、任务点、障碍、地块、车库、补给点、机耕道'], ['F', '地图适配全图'], ['Delete', '删除选中对象']];

export function ShortcutList() {
  return <dl className={s.keys}>{KEYS.flatMap(([k, v]) => [<dt key={`${k}k`}>{k}</dt>, <dd key={`${k}v`}>{v}</dd>])}</dl>;
}

function General() {
  const theme = useDesk((d) => d.theme);
  const setTheme = useDesk((d) => d.setTheme);
  const dock = useDesk((d) => d.dock);
  const setDock = useDesk((d) => d.setDock);
  const motion = useDesk((d) => d.motion);
  const setMotion = useDesk((d) => d.setMotion);
  const themes: [Theme, string][] = [['system', '跟随系统'], ['light', '浅色'], ['dark', '深色']];
  const motions: [Motion, string][] = [['full', '完整'], ['reduced', '减弱']];
  return (
    <div className={s.pageIn}>
      <h2>外观</h2>
      <div className={s.choice}>{themes.map(([k, label]) => <button key={k} type="button" aria-pressed={theme === k} onClick={(e) => themeReveal(() => setTheme(k), e.clientX, e.clientY)}>{label}</button>)}</div>
      <h2>动效</h2>
      <div className={s.choice}>{motions.map(([k, label]) => <button key={k} type="button" aria-pressed={motion === k} onClick={() => setMotion(k)}>{label}</button>)}</div>
      <p className={s.txt} style={{ paddingTop: 8 }}>减弱后界面直接切换，不做过渡。系统设置里关闭动画时也一样。</p>
      <h2>工作区布局</h2>
      <div className={s.choice}>
        <button type="button" aria-pressed={!dock} onClick={() => setDock(false)}>标准布局</button>
        <button type="button" aria-pressed={dock} onClick={() => setDock(true)}>自由布局</button>
      </div>
      <p className={s.txt} style={{ paddingTop: 8 }}>标准布局一次聚焦一件事。自由布局里每个面板都能拖动、合并为标签、拆分或浮动，按工作区记住。</p>
      <h2>快捷键</h2>
      <div style={{ paddingTop: 8 }}><ShortcutList /></div>
    </div>
  );
}

/** Everything about the machine and the harness, in one place, the way a desktop app keeps its settings. */
export function SettingsPage({ route }: { route: Route }) {
  const section = useMemo(() => SECTIONS.find((x) => x.id === route.id) ?? SECTIONS[0], [route.id]);
  const nav = useRef<HTMLElement>(null);
  return (
    <div className={s.main} style={{ flex: 1 }}>
      <TopBar><h1 className={s.pageTitle}>设置</h1></TopBar>
      <div className={s.settings}>
        <nav className={s.settingsNav} aria-label="设置" ref={nav}>
          <Marker host={nav} match='[aria-current="page"]' kind="pill" watch={section.id} />
          {SECTIONS.map((x) => <a key={x.id} href={`#/system/${x.id}`} aria-current={x.id === section.id ? 'page' : undefined}>{x.label}</a>)}
        </nav>
        <div className={s.settingsBody} key={section.id}>{section.panel ? <PanelHost id={section.panel} /> : <div className={s.page}><General /></div>}</div>
      </div>
    </div>
  );
}
