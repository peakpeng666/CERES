import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Icon, type IconName } from './Icon';
import { cx, n1 } from './format';
import { useDesk } from './store';
import { useMedia } from './nav';
import s from './desk.module.css';

/** The disclosure row: one click opens one layer. The content mounts on first open and stays. */
export function Fold({ summary, children, className, status, defaultOpen = false }: {
  summary: ReactNode; children: () => ReactNode; className?: string; status?: string; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [seen, setSeen] = useState(defaultOpen);
  return (
    <div className={className}>
      <button type="button" className={s.disc} aria-expanded={open} data-st={status} onClick={() => { setOpen(!open); setSeen(true); }}>
        <Icon name="right" size={12} className={s.chev} />
        {summary}
      </button>
      <div className={s.fold} data-open={open || undefined} inert={!open}><div>{seen ? children() : null}</div></div>
    </div>
  );
}

export function Row({ lead, title, sub, value, valueSub, onClick, dense, wrap, onEnter, onLeave }: {
  lead?: ReactNode; title: ReactNode; sub?: ReactNode; value?: ReactNode; valueSub?: ReactNode; onClick?: () => void;
  dense?: boolean; wrap?: boolean; onEnter?: () => void; onLeave?: () => void;
}) {
  const cls = cx(s.row, lead === undefined && s.rowNoLead, dense && s.rowDense);
  const inner = (
    <>
      {lead !== undefined && <span className={s.rowLead}>{lead}</span>}
      <span className={cx(s.rowT, wrap && s.rowWrap)}><b>{title}</b>{sub ? <small>{sub}</small> : null}</span>
      <span className={s.rowV}>{value}{valueSub ? <small>{valueSub}</small> : null}</span>
      {onClick ? <Icon name="right" size={12} className={s.rowGo} /> : <span />}
    </>
  );
  return onClick
    ? <button type="button" className={cls} onClick={onClick} onMouseEnter={onEnter} onMouseLeave={onLeave}>{inner}</button>
    : <div className={cls}>{inner}</div>;
}
export const Rows = ({ children }: { children: ReactNode }) => <div className={s.rows}>{children}</div>;

type Skip = false | null | undefined | '' | 0;
export function Facts({ items }: { items: ([string, ReactNode, string?] | Skip)[] }) {
  return (
    <dl className={s.facts}>
      {items.filter(Boolean).map((x) => {
        const [k, v, u] = x as [string, ReactNode, string?];
        return <div key={k}><dt>{k}</dt><dd>{v}{u ? <small>{u}</small> : null}</dd></div>;
      })}
    </dl>
  );
}
export function Kvs({ items }: { items: ([string, ReactNode] | Skip)[] }) {
  return <dl className={s.kvs}>{items.filter(Boolean).flatMap((x) => { const [k, v] = x as [string, ReactNode]; return [<dt key={`${k}-k`}>{k}</dt>, <dd key={`${k}-v`}>{v}</dd>]; })}</dl>;
}

export interface MenuItem { label: string; sub?: string; on?: boolean; disabled?: boolean; icon?: IconName; onSelect?: () => void }
/** Anchored menu; opens above the anchor when it sits low in the window. */
export function useMenu() {
  const [state, setState] = useState<{ items: MenuItem[]; x: number; y: number; up: boolean } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!state) return;
    const close = (e: Event) => { if (!(e.target as Element)?.closest?.(`.${s.menu}`)) setState(null); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setState(null); };
    addEventListener('pointerdown', close); addEventListener('keydown', esc);
    return () => { removeEventListener('pointerdown', close); removeEventListener('keydown', esc); };
  }, [state]);
  const open = (anchor: HTMLElement, items: MenuItem[]) => {
    const r = anchor.getBoundingClientRect();
    const up = r.top > innerHeight * 0.55;
    setState({ items, x: Math.max(8, Math.min(r.left, innerWidth - 348)), y: up ? innerHeight - r.top + 6 : r.bottom + 6, up });
  };
  const node = state ? createPortal(
    <div ref={ref} className={s.menu} role="menu" style={{ left: state.x, ...(state.up ? { bottom: state.y } : { top: state.y }) }}>
      {state.items.map((it, i) => it.label === '-' ? <div key={i} className={s.menuSep} /> : (
        <button key={i} type="button" role="menuitem" disabled={it.disabled} onClick={() => { setState(null); it.onSelect?.(); }}>
          <span>{it.label}{it.sub ? <small>{it.sub}</small> : null}</span>{it.on ? <Icon name="check" size={13} /> : null}
        </button>
      ))}
    </div>, document.body) : null;
  return { open, node };
}

export function TopBar({ children, right, surface }: { children: ReactNode; right?: ReactNode; surface?: string }) {
  const sideOpen = useDesk((d) => d.sideOpen);
  const setSide = useDesk((d) => d.setSide);
  const paneOpen = useDesk((d) => (surface ? d.pane[surface] : false));
  const setPane = useDesk((d) => d.setPane);
  const narrow = useMedia('(max-width: 1060px)');
  return (
    <header className={s.bar}>
      {(!sideOpen || narrow) && <button type="button" className={s.iconBtn} title="任务栏 ⌘B" aria-label="打开任务栏" onClick={() => setSide(true)}><Icon name="side" /></button>}
      <div className={s.barTitle}>{children}</div>
      {right}
      {surface && <button type="button" className={s.iconBtn} title="面板 ⌘J" aria-label="切换面板" aria-pressed={!!paneOpen} onClick={() => setPane(surface, !paneOpen)}><Icon name="pane" /></button>}
    </header>
  );
}

export const money = (v: number | null | undefined) => (v === null || v === undefined ? '—' : n1(v));
