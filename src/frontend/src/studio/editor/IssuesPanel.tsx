import { useState } from 'react';
import { ISSUE_LABEL, type GeometryIssue } from './validation';
import { focusFormField } from './fieldFocus';
import s from './editor.module.css';

const MAX_SHOWN = 200;

/** Local geometry warnings; clicking one selects and zooms to the offending object. Never edits anything. */
export function IssuesPanel({ issues, focused, onLocate }: { issues: GeometryIssue[]; focused: string | null; onLocate: (i: GeometryIssue) => void }) {
  const errors = issues.filter((i) => i.severity === 'error').length;
  const [missing, setMissing] = useState<string | null>(null);
  const locate = (i: GeometryIssue) => {
    setMissing(null);
    if (i.field && !i.target) {
      const label = i.field.label;
      focusFormField(i.field, (ok) => { if (!ok) setMissing(`未找到「${label}」输入框：请先打开检查器面板。`); });
      return;
    }
    onLocate(i);
  };
  return (
    <details className={s.panel} open aria-label="几何校验">
      <summary className={s.panelSummary}>几何校验 {issues.length ? `· ${errors} 错误 / ${issues.length - errors} 警告` : '· 未发现问题'}</summary>
      {issues.length > 0 && <div className={s.hint}>仅提示，不会自动修改地图、车辆数、容量或作业幅宽。点击定位。</div>}
      <ul className={s.issueList}>
        {issues.slice(0, MAX_SHOWN).map((i) => (
          <li key={i.key}>
            <button type="button" data-severity={i.severity} aria-current={focused === i.key} disabled={!i.target && !i.bounds && !i.field} onClick={() => locate(i)}>
              <strong>{i.severity === 'error' ? '✕ ' : '⚠ '}{ISSUE_LABEL[i.code]}</strong>
              <span>{i.message}</span>
            </button>
          </li>
        ))}
      </ul>
      {missing && <div className={s.hint} role="status">{missing}</div>}
      {issues.length > MAX_SHOWN && <div className={s.hint}>另有 {issues.length - MAX_SHOWN} 项未列出</div>}
    </details>
  );
}
