import { describe, expect, it } from 'vitest';
import { LabApiError } from '../lab/api';
import { executionErrorMessage, fieldTrafficLabel, unacceptedPlanReason } from './execution';
import type { FleetInstance } from './types';

describe('execution admission recovery', () => {
  it('labels actual traffic policy and keeps missing historical evidence unknown', () => {
    expect(fieldTrafficLabel({ traffic_mode: 'coordinated' })).toBe('并行协调');
    expect(fieldTrafficLabel({ traffic_mode: 'serial' })).toBe('逐车串行');
    expect(fieldTrafficLabel({ traffic: { policy: 'coordinated_passage_v1' } })).toBe('并行协调');
    expect(fieldTrafficLabel({ traffic: { policy: 'exclusive_mission_v1' } })).toBe('逐车串行');
    expect(fieldTrafficLabel({})).toBe('交通方式未记录');
    expect(fieldTrafficLabel({ traffic_mode: null })).toBe('交通方式未记录');
  });

  it('keeps 422 codes visible and offers an explicit recovery path', () => {
    for (const code of ['heterogeneous_fleet_execution', 'curvature_constrained_execution', 'unsupported_vehicle_asset']) {
      const message = executionErrorMessage(new LabApiError('后端拒绝', code, 422));
      expect(message).toContain('422');
      expect(message).toContain(code);
      expect(message).toContain('恢复');
    }
  });

  it('explains 409 asset conflicts without rewriting the historical snapshot', () => {
    const message = executionErrorMessage(new LabApiError('哈希不一致', 'asset_version_conflict', 409));
    expect(message).toContain('409');
    expect(message).toContain('显式重新绑定');
    expect(message).toContain('历史方案');
  });

  it('retains blocked resupply input while withholding execution promotion', () => {
    const instance = { unload_points: [[4, 5]] } as FleetInstance;
    expect(unacceptedPlanReason(instance)).toContain('旧模型');
    expect(unacceptedPlanReason(instance, 'urp.fleet-model/2')).toBeNull();
    expect(instance.unload_points).toEqual([[4, 5]]);
  });

  it('uses shutdown-specific timeout recovery instead of claiming a start may have happened', () => {
    const error = new LabApiError('关闭超时', 'timeout', 0);
    expect(executionErrorMessage(error, 'stop')).toContain('关闭请求');
    expect(executionErrorMessage(error, 'stop')).not.toContain('启动请求');
    expect(executionErrorMessage(error)).toContain('启动请求');
  });
});
