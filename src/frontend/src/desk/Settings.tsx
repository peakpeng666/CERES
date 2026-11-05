import { useMemo } from 'react';
import type { Route } from '../app/router';
import { PanelHost, type PanelId } from '../studio/panelRegistry';
import { useDesk, type Theme } from './store';
import { TopBar } from './ui';
import s from './desk.module.css';

const SECTIONS: { id: string; label: string; panel?: PanelId }[] = [
  { id: 'general', label: '通用' },
  { id: 'models', label: '模型与用量', panel: 'models' },
  { id: 'capabilities', label: '能力', panel: 'capabilities' },
  { id: 'tools', label: '工具与闸门', panel: 'tools' },
  { id: 'evals', label: '智能体评测', panel: 'evals' },
  { id: 'services', label: '服务', panel: 'services' },
  { id: 'logs', label: '日志', panel: 'console' },
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
  const themes: [Theme, string][] = [['system', '跟随系统'], ['light', '浅色'], ['dark', '深色']];
  return (
    <div className={s.pageIn}>
      <h2>外观</h2>
      <div className={s.choice}>{themes.map(([k, label]) => <button key={k} type="button" aria-pressed={theme === k} onClick={() => setTheme(k)}>{label}</button>)}</div>
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
  return (
    <div className={s.main} style={{ flex: 1 }}>
      <TopBar><h1 className={s.pageTitle}>设置</h1></TopBar>
      <div className={s.settings}>
        <nav className={s.settingsNav} aria-label="设置">
          {SECTIONS.map((x) => <a key={x.id} href={`#/system/${x.id}`} aria-current={x.id === section.id ? 'page' : undefined}>{x.label}</a>)}
        </nav>
        <div className={s.settingsBody}>{section.panel ? <PanelHost id={section.panel} /> : <div className={s.page}><General /></div>}</div>
      </div>
    </div>
  );
}
