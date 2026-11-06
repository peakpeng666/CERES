import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SessionEvidence } from './SessionEvidence';
import type { SimResult } from './replay';

const record = (extra: Partial<SimResult> = {}) => ({
  state: 'succeeded', traffic_mode: 'serial', check: { valid: true, dynamics: { valid: true } },
  physics_audit: { valid: true }, physics_runtime: { valid: true }, ...extra,
}) as SimResult;
const html = (result: SimResult) => renderToStaticMarkup(createElement(SessionEvidence, { result }));
const beforeDetails = (markup: string) => markup.split('<details>')[0];

describe('compact session evidence', () => {
  it('keeps measured status visible and moves general methodology to the project link', () => {
    const markup = html(record({ physics: { schema: 'model/v1', assumptions: { generic: 'Long methodology from the raw record' } },
      truth_timing: { scope: 'Long clock explanation from the raw record' } }));
    const visible = beforeDetails(markup);
    expect(visible).toContain('配置 通过');
    expect(visible).toContain('运行 通过');
    expect(visible).toContain('动力学检查');
    expect(visible).not.toContain('model/v1');
    expect(markup).toContain('<details><summary>本次核验明细</summary>');
    expect(markup).not.toContain('<details open');
    expect(markup).not.toContain('Long methodology');
    expect(markup).not.toContain('Long clock explanation');
    expect(markup).toContain('README.md#仿真与引擎比较');
  });

  it('keeps an actual error and incomplete cleanup outside the collapsed details', () => {
    const markup = html(record({
      physics_runtime: { valid: false, errors: ['物理库指纹不符'] },
      session_report: { cleanup_status: 'FAIL', remaining_owned_processes: [{ pid: 1 }] },
    }));
    const visible = beforeDetails(markup);
    expect(visible).toContain('物理库指纹不符');
    expect(visible).toContain('会话清理未完成');
    expect(visible).toContain('role="alert"');
  });

  it('does not promote missing evidence or missing library hashes into success or zero', () => {
    const markup = html(record({ check: undefined, physics_audit: undefined, physics_runtime: undefined }));
    expect(beforeDetails(markup)).toContain('配置 未知');
    expect(beforeDetails(markup)).toContain('运行 未知');
    expect(markup).not.toContain('0 个已映射库带哈希');
  });

  it('does not relabel a passed execution when only session cleanup failed', () => {
    const markup = html(record({ session_report: { cleanup_status: 'FAIL', errors: ['ROS 进程尚未退出'] } }));
    const visible = beforeDetails(markup);
    expect(visible).toContain('运行 通过');
    expect(visible).toContain('会话清理未完成');
    expect(visible).not.toContain('执行或证据核验未通过');
    expect(markup).toContain('ROS 进程尚未退出');
  });

  it('keeps a failed observation check visible when the broad status is absent', () => {
    const markup = html(record({ check: { nonideal_observations: { chain_replay: { valid: false } } } }));
    expect(beforeDetails(markup)).toContain('执行或证据核验未通过');
  });
});
