import { fmt } from '../../lab/api';
import { Notice } from '../../lab/ui';
import type { SimResult } from './replay';
import { fieldTrafficLabel } from '../execution';
import { arrivalLine, lateLine } from '../../desk/arrivals';
import s from '../panels/panels.module.css';

const evidenceState = (valid: boolean | undefined) => valid === true ? '通过' : valid === false ? '失败' : '未知';

export function SessionEvidence({ result }: { result: SimResult }) {
  const dynamics = result.check?.dynamics;
  const vehicles = dynamics?.vehicles ?? [];
  const nonideal = dynamics?.execution_mode === 'nonideal';
  const estimated = !!result.execution?.localization?.gnss;
  const gaps = vehicles.map((item) => item.max_truth_gap_s).filter((value): value is number => typeof value === 'number');
  const finalDrift = vehicles.map((item) => item.odom_position_final_m).filter((value): value is number => typeof value === 'number');
  const maxDrift = vehicles.map((item) => item.odom_position_max_m).filter((value): value is number => typeof value === 'number');
  const runtimeLibraries = (result.physics_runtime?.loaded_library_sha256 ?? {}) as Record<string, string>;
  const executionErrors = [...new Set([...(result.errors ?? []), ...(result.check?.errors ?? []),
    ...(result.physics_audit?.errors ?? []), ...(result.physics_runtime?.errors ?? [])])];
  const cleanupErrors = result.session_report?.errors ?? [];
  const errors = [...new Set([...executionErrors, ...cleanupErrors])];
  const failed = result.state === 'failed' || [result.check?.valid, result.physics_audit?.valid, result.physics_runtime?.valid,
    dynamics?.valid, result.check?.nonideal_observations?.chain_replay?.valid, result.check?.task_truth?.valid, result.time_windows?.all_on_time].includes(false) || executionErrors.length > 0;
  const cleanupFailed = result.session_report?.cleanup_status === 'FAIL' || !!result.session_report?.remaining_owned_processes?.length;
  return (
    <section className={s.sessionEvidence} aria-label="逐会话物理证据">
      <div className={s.kv}>
        <span>交通方式</span><b>{fieldTrafficLabel(result)}</b>
        {result.time_windows && <><span>实测时间窗</span><b>{arrivalLine(result.time_windows)}{result.time_windows.all_on_time ? '' : ` · ${lateLine(result.time_windows)}`}</b></>}
        <span>物理核验</span><b>配置 {evidenceState(result.physics_audit?.valid)} · 运行 {evidenceState(result.physics_runtime?.valid)}</b>
        <span>动力学检查</span><b>{evidenceState(dynamics?.valid)}</b>
      </div>
      {failed && <Notice tone="error">执行或证据核验未通过{executionErrors[0] ? `：${executionErrors[0]}` : '。'} 查看下方详细证据后再判断结果。</Notice>}
      {cleanupFailed && <Notice tone="error">会话清理未完成。请在仿真面板核对关闭报告。</Notice>}
      <details>
        <summary>本次核验明细</summary>
        <div className={s.kv}>
          <span>物理模型</span><b>{result.physics?.schema ?? '历史记录未提供'}</b>
          <span>源时间证据</span><b>{result.truth_timing ? '已记录' : '未记录'}</b>
          <span>采样质量</span><b>{gaps.length ? `最大真值间隔 ${fmt.num(Math.max(...gaps), 3)} s` : '采样质量未知'}</b>
          <span>轮式里程计漂移</span><b>{finalDrift.length ? `末端最大 ${fmt.num(Math.max(...finalDrift), 3)} m${maxDrift.length ? ` · 全轨迹最大 ${fmt.num(Math.max(...maxDrift), 3)} m` : ''}` : '历史记录未提供'}</b>
          <span>无滑移模型一致性</span><b>{nonideal ? '非理想执行：不按无滑移模型判定' : dynamics?.odometry_contract
            ? `${vehicles.length && vehicles.every(v => v.no_slip_consistent === true) ? '通过' : '未通过'}；位置 ≤ ${dynamics.odometry_contract.max_position_error_m} m，航向 ≤ ${dynamics.odometry_contract.max_yaw_error_rad} rad`
            : '未记录'}</b>
          <span>控制定位</span><b>{result.execution ? (estimated ? '合成观测融合估计' : '真值对照') : '真值参考'}</b>
          {estimated && <><span>观测 → 控制重建</span><b>{evidenceState(result.check?.nonideal_observations?.chain_replay?.valid)}</b></>}
          {result.execution && <><span>真值到点与停留</span><b>{evidenceState(result.check?.task_truth?.valid)}</b></>}
          <span>运行库证据</span><b>{result.physics_runtime?.loaded_library_sha256 != null ? `${Object.keys(runtimeLibraries).length} 个已映射库带哈希` : '未记录'}</b>
          <span>关闭报告</span><b>整体 {result.session_report?.status ?? '未记录'} · 清理 {result.session_report?.cleanup_status ?? '未记录'} · 执行 {result.session_report?.execution_state ?? result.state}</b>
        </div>
        {errors.length > 0 && <ul>{errors.map((error) => <li key={error} className={s.hint}>{error}</li>)}</ul>}
      </details>
      <a className={s.link} href="https://github.com/peakpeng666/URP/blob/main/setup/README.md#仿真与引擎比较" target="_blank" rel="noreferrer">比较方法</a>
    </section>
  );
}
