import { useEffect, useState } from 'react';
import type { XY } from '../../lab/types';
import type { FleetInstance } from '../types';
import { ITEM_LABEL, ITEM_TYPES, allItems, boxOf, itemPoints, setItemBox, setItemPoint, translateItems, type Box, type ItemRef, type ItemType } from './selection';
import s from './editor.module.css';

export interface SelectionPanelProps {
  inst: FleetInstance;
  refs: ItemRef[];
  editable: boolean;
  onEdit: (fn: (i: FleetInstance) => FleetInstance) => void;
  onSelectRefs: (refs: ItemRef[]) => void;
}

/** Numeric field in metres: edits a text draft, reports a finite number on Enter / blur, else shows an error. */
function NumField({ label, value, onCommit, disabled, min }: { label: string; value: number; onCommit: (v: number) => void; disabled?: boolean; min?: number }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const n = Number(text);
  const bad = text.trim() === '' || !Number.isFinite(n) || (min !== undefined && n <= min);
  const commit = () => {
    if (!bad && n !== value) onCommit(n);
    else if (bad) setText(String(value));
  };
  return (
    <label className={s.numField}>
      <span>{label}</span>
      <input
        type="text"
        inputMode="decimal"
        aria-label={`${label}（米）`}
        aria-invalid={bad}
        disabled={disabled}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setText(String(value)); }}
      />
      <em>m</em>
    </label>
  );
}

export function SelectionPanel({ inst, refs, editable, onEdit, onSelectRefs }: SelectionPanelProps) {
  const [dx, setDx] = useState('0');
  const [dy, setDy] = useState('0');
  const counts = ITEM_TYPES.map((t) => [t, allItems(inst, [t]).length] as const).filter(([, n]) => n > 0);
  const one = refs.length === 1 ? refs[0] : null;
  const pts = one ? itemPoints(inst, one) : null;
  const isPoint = one && (one.type === 'task' || one.type === 'depot' || one.type === 'unload');
  const box: Box | null = pts && !isPoint ? boxOf(pts) : null;
  const mx = Number(dx);
  const my = Number(dy);
  const moveOk = Number.isFinite(mx) && Number.isFinite(my) && (mx !== 0 || my !== 0);
  return (
    <details className={s.panel} open aria-label="选择与精确输入">
      <summary className={s.panelSummary}>选择与精确输入{refs.length ? ` · 已选 ${refs.length}` : ''}</summary>
      <div className={s.panelTitle}>按类型选择</div>
      <div className={s.chips}>
        {counts.map(([t, n]) => (
          <button key={t} type="button" onClick={() => onSelectRefs(allItems(inst, [t as ItemType]))}>{ITEM_LABEL[t]} ({n})</button>
        ))}
        {refs.length > 0 && <button type="button" onClick={() => onSelectRefs([])}>清除</button>}
      </div>
      <div className={s.hint}>框选工具或 Alt/⌘ + 拖动框选，⌘/Ctrl + 点击加减选</div>
      {refs.length > 0 && <div className={s.panelTitle}>已选 {refs.length} 项{one ? ` · ${ITEM_LABEL[one.type]} ${one.type === 'depot' ? `D${Number(one.id) + 1}` : one.type === 'unload' ? `U${Number(one.id) + 1}` : one.id}` : ''}</div>}
      {one && pts && isPoint && (
        <div className={s.fields}>
          <NumField label="X" value={pts[0][0]} disabled={!editable} onCommit={(v) => onEdit((i) => setItemPoint(i, one, [v, pts[0][1]] as XY))} />
          <NumField label="Y" value={pts[0][1]} disabled={!editable} onCommit={(v) => onEdit((i) => setItemPoint(i, one, [pts[0][0], v] as XY))} />
        </div>
      )}
      {one && box && (
        <div className={s.fields}>
          <NumField label="X" value={box.x} disabled={!editable} onCommit={(v) => onEdit((i) => setItemBox(i, one, { ...box, x: v }))} />
          <NumField label="Y" value={box.y} disabled={!editable} onCommit={(v) => onEdit((i) => setItemBox(i, one, { ...box, y: v }))} />
          <NumField label="宽" value={box.w} min={0} disabled={!editable} onCommit={(v) => onEdit((i) => setItemBox(i, one, { ...box, w: v }))} />
          <NumField label="高" value={box.h} min={0} disabled={!editable} onCommit={(v) => onEdit((i) => setItemBox(i, one, { ...box, h: v }))} />
        </div>
      )}
      {refs.length > 0 && editable && (
        <div className={s.fields}>
          <label className={s.numField}><span>ΔX</span><input aria-label="批量平移 X（米）" value={dx} onChange={(e) => setDx(e.target.value)} /><em>m</em></label>
          <label className={s.numField}><span>ΔY</span><input aria-label="批量平移 Y（米）" value={dy} onChange={(e) => setDy(e.target.value)} /><em>m</em></label>
          <button type="button" disabled={!moveOk} onClick={() => { onEdit((i) => translateItems(i, refs, mx, my)); setDx('0'); setDy('0'); }}>平移</button>
        </div>
      )}
    </details>
  );
}
