import type { TimeWindowRecord, TimeWindowRow } from '../lab/types';
import { n1 } from './format';

/** Wall clock of a mission time; plain seconds when the mission has no start time. */
export function atClock(start: string | null | undefined, seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—';
  if (!start) return `${n1(seconds)} s`;
  const [h, m] = start.split(':').map(Number);
  const total = h * 3600 + m * 60 + Math.round(seconds);
  const part = (v: number) => String(v).padStart(2, '0');
  return `${part(Math.floor(total / 3600) % 24)}:${part(Math.floor((total % 3600) / 60))}:${part(total % 60)}`;
}

export function windowText(r: TimeWindowRow, start?: string | null): string {
  const late = r.latest_s === null ? '不限' : atClock(start, r.latest_s);
  return r.earliest_s > 0 ? `${atClock(start, r.earliest_s)} – ${late}` : `截止 ${late}`;
}

/** The verdict in a few words: how many tasks kept their window. */
export function arrivalLine(w: TimeWindowRecord | null | undefined): string {
  if (!w) return '';
  if (w.evaluated === false) return '未能计算到达时间';
  return w.all_on_time ? `${w.total} 个任务全部按时` : `${w.on_time}/${w.total} 个任务按时`;
}

export function lateOf(r: TimeWindowRow): string {
  return r.arrival_s === null ? `${r.label} 未到达` : `${r.label} 晚 ${n1(-(r.slack_s ?? 0))} s`;
}

/** Which tasks missed, named; a count alone would leave the reader to find them. */
export function lateLine(w: TimeWindowRecord | null | undefined, limit = 4): string {
  const late = w?.late ?? [];
  const more = (w?.total ?? 0) - (w?.on_time ?? 0) - Math.min(limit, late.length);
  return late.slice(0, limit).map(lateOf).join('、') + (more > 0 ? ` 等，另有 ${more} 个` : '');
}
