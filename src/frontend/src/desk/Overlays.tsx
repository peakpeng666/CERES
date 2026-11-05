import { useEffect, useMemo } from 'react';
import { Command } from 'cmdk';
import { lab, useLabResource } from '../lab/api';
import { useAgent } from '../agent/store';
import { PANELS, type PanelId } from '../studio/panelRegistry';
import { useStudio } from '../studio/store';
import { useAssets } from '../studio/assets/store';
import st from '../studio/studio.module.css';
import { useDesk } from './store';
import { ShortcutList } from './Settings';
import { sessionLabel } from './nav';
import s from './desk.module.css';

const PLACES: [string, string][] = [['新任务', '#/agent'], ['地图', '#/maps'], ['资产', '#/assets'], ['实验', '#/experiments'], ['运行记录', '#/runs'], ['CVRP 数值任务', '#/jobs'], ['仿真', '#/sim'], ['算法库', '#/algorithms'], ['CVRPLIB 算例', '#/library/cvrplib'], ['真实田块', '#/library/fields'], ['文献', '#/library/references'], ['BPP 历史结果', '#/bpp'], ['设置', '#/system'], ['模型与用量', '#/system/models'], ['能力', '#/system/capabilities'], ['工具与闸门', '#/system/tools'], ['智能体评测', '#/system/evals'], ['服务', '#/system/services'], ['日志', '#/system/logs']];

export function Palette() {
  const studio = useStudio();
  const setDock = useDesk((d) => d.setDock);
  const dock = useDesk((d) => d.dock);
  const setOverlay = useDesk((d) => d.setOverlay);
  const setTheme = useDesk((d) => d.setTheme);
  const sessions = useAgent((x) => x.sessions);
  const scenarios = useLabResource('palette-scenarios', () => lab.scenarios());
  const close = () => studio.setPalette(false);
  const run = (fn: () => void) => () => { fn(); close(); };
  const go = (hash: string) => run(() => { location.hash = hash; });
  const open = (id: PanelId) => run(() => window.dispatchEvent(new CustomEvent('studio:open-panel', { detail: id })));
  const fleet = useMemo(() => (scenarios.data?.scenarios ?? []).slice(0, 200), [scenarios.data]);
  return (
    <div className={st.paletteBackdrop} onMouseDown={close}>
      <div className={st.palette} onMouseDown={(e) => e.stopPropagation()}>
        <Command label="命令面板" loop>
          <Command.Input autoFocus placeholder="搜索任务、地图、面板或操作…" onKeyDown={(e) => e.key === 'Escape' && close()} />
          <Command.List>
            <Command.Empty>没有匹配项</Command.Empty>
            <Command.Group heading="前往">
              {PLACES.map(([label, hash]) => <Command.Item key={hash} onSelect={go(hash)}>{label}</Command.Item>)}
            </Command.Group>
            <Command.Group heading="操作">
              <Command.Item onSelect={go('#/maps')}>一句话生成任务地图</Command.Item>
              <Command.Item onSelect={run(() => { location.hash = '#/assets'; useAssets.getState().openImport(); })}>导入资产（地图 / 车辆 / 设施）</Command.Item>
              <Command.Item onSelect={open('replay')}>回放仿真</Command.Item>
              <Command.Item onSelect={open('results')}>查看实验结果并导出</Command.Item>
              <Command.Item onSelect={open('figures')}>科研图表（期刊版式 / 论文图包）</Command.Item>
              <Command.Item onSelect={run(() => void studio.solve())}>求解并对比推荐的算法</Command.Item>
              <Command.Item onSelect={run(() => window.dispatchEvent(new Event('studio:fit')))}>地图适配全图</Command.Item>
              <Command.Item onSelect={run(studio.undo)}>撤销</Command.Item>
              <Command.Item onSelect={run(studio.redo)}>重做</Command.Item>
              <Command.Item onSelect={run(() => { location.hash = '#/experiments'; studio.setStudy(null); })}>新建研究课题</Command.Item>
              <Command.Item onSelect={run(() => setDock(!dock))}>{dock ? '回到标准布局' : '切换到自由布局'}</Command.Item>
              <Command.Item onSelect={run(() => setOverlay('help'))}>快捷键</Command.Item>
              <Command.Item onSelect={run(() => setTheme('light'))}>外观：浅色</Command.Item>
              <Command.Item onSelect={run(() => setTheme('dark'))}>外观：深色</Command.Item>
              <Command.Item onSelect={run(() => setTheme('system'))}>外观：跟随系统</Command.Item>
            </Command.Group>
            <Command.Group heading="任务">
              {sessions.map((x) => <Command.Item key={x.session_id} value={`${sessionLabel(x)} ${x.title} ${x.session_id}`} onSelect={go(`#/agent/${encodeURIComponent(x.session_id)}`)}>{sessionLabel(x)}</Command.Item>)}
            </Command.Group>
            <Command.Group heading="面板">
              {(Object.keys(PANELS) as PanelId[]).map((id) => <Command.Item key={id} onSelect={open(id)}>打开 {PANELS[id].title}</Command.Item>)}
            </Command.Group>
            <Command.Group heading="地图">
              {fleet.map((x) => <Command.Item key={x.id} value={`${x.title} ${x.id}`} onSelect={go(`#/plan/${encodeURIComponent(x.id)}`)}>{x.title}</Command.Item>)}
            </Command.Group>
          </Command.List>
        </Command>
      </div>
    </div>
  );
}

export function Overlays() {
  const overlay = useDesk((d) => d.overlay);
  const setOverlay = useDesk((d) => d.setOverlay);
  useEffect(() => {
    if (!overlay) return;
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOverlay(null); } };
    addEventListener('keydown', esc, true);
    return () => removeEventListener('keydown', esc, true);
  }, [overlay, setOverlay]);
  if (!overlay) return null;
  return (
    <div className={s.backdrop} onMouseDown={() => setOverlay(null)}>
      <div className={s.sheet} role="dialog" aria-modal="true" aria-label="快捷键" onMouseDown={(e) => e.stopPropagation()}>
        <h2>快捷键</h2>
        <ShortcutList />
      </div>
    </div>
  );
}
