import { LabApiError } from '../lab/api';
import type { Solution } from '../lab/types';
import type { FieldTrafficMode, FleetInstance } from './types';

export function fieldTrafficLabel(record: { traffic_mode?: FieldTrafficMode | null; traffic?: { policy?: string | null } | null }): string {
  if (record.traffic_mode === 'coordinated' || record.traffic?.policy === 'coordinated_passage_v1') return '并行协调';
  if (record.traffic_mode === 'serial' || record.traffic?.policy === 'exclusive_mission_v1') return '逐车串行';
  return '交通方式未记录';
}

export function planModelSchema(run: Pick<Solution, 'raw_result'>): string | undefined {
  return run.raw_result?.model_contract?.schema;
}

export function researchAcceptanceIssues(instance: FleetInstance | undefined, modelSchema?: string): string[] {
  const issues: string[] = [];
  const blocked = unacceptedPlanReason(instance, modelSchema);
  if (blocked) issues.push(blocked);
  if (instance?.vehicles.fleet?.some((item) => (item.min_turn_radius_m ?? 0) > 0)) issues.push('曲率方案经过采样几何检查，尚不支持田间差速仿真执行；不能当作车辆动力学验证。');
  return issues;
}

export function unacceptedPlanReason(instance: FleetInstance | undefined, modelSchema?: string): string | null {
  if (instance?.unload_points?.length && modelSchema !== 'urp.fleet-model/2') {
    return '该补给方案来自旧模型或缺少版本证据，请用当前求解器重新求解并验算后执行；原记录保留';
  }
  return null;
}

export function executionErrorMessage(error: unknown, operation: 'start' | 'stop' = 'start'): string {
  const e = error instanceof Error ? error : new Error(String(error));
  const code = error instanceof LabApiError ? error.code : '';
  const status = error instanceof LabApiError ? error.status : 0;
  if (code === 'heterogeneous_fleet_execution') return `${e.message}（422 heterogeneous_fleet_execution）。恢复：保留该方案用于规划研究；如需执行，请在副本中改为同构内置车型并重新求解、验算`;
  if (code === 'curvature_constrained_execution') return `${e.message}（422 curvature_constrained_execution）。恢复：保留曲率输入和结果；如需执行，请新建最小转弯半径为 0 的同构差速方案，不由界面静默改写`;
  if (code === 'unsupported_vehicle_asset') return `${e.message}（422 unsupported_vehicle_asset）。恢复：在检查器选择当前内置田间差速车型并重新求解；不会替换成其他车体`;
  if (status === 409) return `${e.message}（409 资产版本 / 哈希冲突）。恢复：核对资产当前版本，显式重新绑定并求解；历史方案仍按原快照保存`;
  if (code === 'timeout' && operation === 'stop') return `${e.message}。关闭请求可能已到达后端：先刷新会话与关闭报告，确认进程回收状态后再操作；不要重复关闭或用强杀掩盖结果`;
  if (code === 'timeout') return `${e.message}。启动请求可能已到达后端：先刷新当前会话状态，确认 idle 后再重试，避免重复启动`;
  if (code === 'network') return `${e.message}。未把窗口打开或请求发出当作实验成功；服务恢复后先刷新会话状态`;
  return e.message;
}
