import { describe, expect, it } from 'vitest';
import type { TimeWindowRecord, TimeWindowRow } from '../lab/types';
import { arrivalLine, atClock, lateLine, lateOf, windowText } from './arrivals';

const row = (over: Partial<TimeWindowRow>): TimeWindowRow => ({
  task: 1, label: 'A3', vehicle: '① 号车', earliest_s: 0, latest_s: 1800, arrival_s: 1834.2, start_s: 1834.2, slack_s: -34.2, on_time: false, ...over,
});

describe('arrival text', () => {
  it('reads mission seconds on the wall clock when the mission has a start', () => {
    expect(atClock('07:30', 5400)).toBe('09:00:00');
    expect(atClock('23:30', 3600)).toBe('00:30:00');
    expect(atClock(null, 12.34)).toBe('12.3 s');
    expect(atClock('07:30', null)).toBe('—');
  });

  it('states the window the way a person would', () => {
    expect(windowText(row({}), '07:30')).toBe('截止 08:00:00');
    expect(windowText(row({ earliest_s: 600 }), null)).toBe('600.0 s – 1,800.0 s');
    expect(windowText(row({ earliest_s: 600, latest_s: null }), null)).toBe('600.0 s – 不限');
  });

  it('names late tasks and says how many are left out', () => {
    const w: TimeWindowRecord = { evaluated: true, total: 12, on_time: 7, all_on_time: false, late: [row({}), row({ label: 'B1', arrival_s: null, slack_s: null })] };
    expect(arrivalLine(w)).toBe('7/12 个任务按时');
    expect(lateLine(w)).toBe('A3 晚 34.2 s、B1 未到达 等，另有 3 个');
    expect(lateOf(row({}))).toBe('A3 晚 34.2 s');
  });

  it('never reads an uncomputed record as on time', () => {
    expect(arrivalLine({ evaluated: false, reason: '没有路线' })).toBe('未能计算到达时间');
    expect(arrivalLine({ evaluated: true, total: 8, on_time: 8, all_on_time: true, late: [] })).toBe('8 个任务全部按时');
    expect(arrivalLine(null)).toBe('');
  });
});
