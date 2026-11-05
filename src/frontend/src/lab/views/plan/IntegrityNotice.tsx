import type { Solution } from '../../types';
import { Notice } from '../../ui';
import { identityState, quarantineReason } from './resultIntegrity';

/** Quarantined: error banner with the recorded reason. Unknown: a plain "身份未核验" note, no inferred truth. */
export function IntegrityNotice({ run }: { run: Solution | null | undefined }) {
  const state = identityState(run);
  if (!run || state === 'verified') return null;
  if (state === 'quarantined') return <Notice tone="error">已隔离：{quarantineReason(run)}</Notice>;
  if (run.state !== 'done') return null;
  return <Notice tone="warn">身份未核验：该记录没有求解器源码身份核对结果，不能视为已核验；数值按原记录显示。</Notice>;
}
