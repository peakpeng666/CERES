import type { Solution } from '../../types';
import { Notice } from '../../ui';
import { identityState } from './resultIntegrity';

/** Quarantined: error banner with the recorded reason. Unknown: a plain "身份未核验" note, no inferred truth. */
export function IntegrityNotice({ run }: { run: Solution | null | undefined }) {
  const state = identityState(run);
  if (!run || state === 'verified') return null;
  if (state === 'quarantined') return <Notice tone="error">
    <strong>已隔离：源码身份核对未通过。</strong> 本次结果不能作为有效结果。
    <details>
      <summary>原因与恢复步骤</summary>
      <p>{run.error || '原记录未提供具体原因。'}</p>
      {run.process?.evidence && <p>证据：{run.process.evidence}</p>}
      <p>核对求解器环境后重新求解；原记录继续保留。</p>
    </details>
  </Notice>;
  if (run.state !== 'done') return null;
  return <Notice tone="warn">
    <strong>身份未核验。</strong> 数值按原记录显示。
    <details>
      <summary>核验范围与下一步</summary>
      <p>该记录缺少求解器源码身份核对结果，不能视为已核验。需要可核验结果时，可使用当前环境重新求解。</p>
    </details>
  </Notice>;
}
