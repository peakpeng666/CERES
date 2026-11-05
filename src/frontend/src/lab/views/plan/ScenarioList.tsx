import { useRef, useState } from 'react';
import { importScenarioOnce } from '../../pendingWrites';
import { FileUp, RefreshCw } from 'lucide-react';
import { buildHash, navigate } from '../../../app/router';
import { lab } from '../../api';
import type { ScenarioSummary } from '../../types';
import { Badge, Button, EmptyState, Notice, Spinner } from '../../ui';
import { groupScenarios } from './planModel';
import p from '../../canvas/panes.module.css';

export function FrameBadge({ frame }: { frame: ScenarioSummary['frame'] }) {
  return frame.kind === 'map' ? (
    <Badge tone="teal" title="ROS map 坐标系，单位米，附官方地图">
      地图·米 · {frame.world}
    </Badge>
  ) : frame.kind === 'field' ? (
    <Badge tone="green" title="实地局部坐标，单位米">实地·米</Badge>
  ) : (
    <Badge tone="gray" title="基准算例坐标，无物理单位，不能直接仿真">
      基准·无量纲
    </Badge>
  );
}

export function ScenarioList({
  scenarios,
  error,
  loading,
  selected,
  onReload,
}: {
  scenarios: ScenarioSummary[] | null;
  error: Error | null;
  loading: boolean;
  selected: string | null;
  onReload: () => void;
}) {
  const file = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setImportError(null);
    setImporting(true);
    try {
      let parsed: unknown;
      try {
        parsed = JSON.parse(await f.text());
      } catch {
        throw new Error(`${f.name} 不是有效的 JSON 文件`);
      }
      const created = await importScenarioOnce(parsed, (doc) => lab.importScenario(doc));
      onReload();
      navigate(buildHash('plan', created.id));
    } catch (e) {
      setImportError((e as Error).message);
    } finally {
      setImporting(false);
      if (file.current) file.current.value = '';
    }
  };

  return (
    <nav aria-label="场景列表">
      <div className={p.sideHead}>
        <h2 className={p.sideTitle}>场景</h2>
        <Button size="sm" variant="plain" icon={RefreshCw} onClick={onReload} title="刷新场景列表" aria-label="刷新场景列表" />
        <Button size="sm" icon={FileUp} loading={importing} onClick={() => file.current?.click()} title="导入 JSON 场景（由网关校验）">
          导入
        </Button>
        <input
          ref={file}
          type="file"
          accept="application/json,.json"
          hidden
          onChange={(e) => void onFile(e.target.files?.[0])}
        />
      </div>
      {importError && (
        <div style={{ padding: '0 4px 8px' }}>
          <Notice tone="error">
            <strong>导入失败：</strong>
            {importError}
          </Notice>
        </div>
      )}
      {error && !scenarios && (
        <Notice tone="error">
          场景列表读取失败：{error.message}
        </Notice>
      )}
      {loading && !scenarios && !error && (
        <div style={{ padding: 12 }}>
          <Spinner label="读取场景" />
        </div>
      )}
      {scenarios && scenarios.length === 0 && <EmptyState title="网关没有提供场景" detail="请检查网关日志，或导入一个 JSON 场景。" />}
      {scenarios &&
        groupScenarios(scenarios).map((g) => (
          <div key={g.cls}>
            <h3 className={p.groupLabel}>{g.label}</h3>
            <ul className={p.items}>
              {g.items.map((sc) => (
                <li key={sc.id}>
                  <a className={p.item} href={buildHash('plan', sc.id)} aria-current={sc.id === selected ? 'true' : undefined} title={sc.summary}>
                    <span className={p.itemTitle}>{sc.title}</span>
                    <span className={p.itemSub}>
                      {sc.id} · {sc.task_count} 个任务{sc.node_count != null ? ` · ${sc.node_count} 个节点` : ''}
                    </span>
                    <span className={p.itemMeta}>
                      <FrameBadge frame={sc.frame} />
                      {sc.sim?.supported && (
                        <Badge tone="green" title={`可在 ${sc.sim.world ?? ''} 世界中仿真`}>
                          可仿真
                        </Badge>
                      )}
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        ))}
    </nav>
  );
}
