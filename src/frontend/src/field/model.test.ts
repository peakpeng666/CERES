import { describe, expect, it } from 'vitest';
import { BASE, basisOf, fmt, hours, implementFits, lineOperations, platformsFor, reconcile } from './model';
import { cell } from './Coverage';
import { planVerdict } from './PlanSide';
import type { Catalog, Environment, Implement, Mission, Platform } from './types';

const sourced = (v: number, s = 'a') => ({ v, s });
const platform = (id: string, cls: string, hitch: string[], tier: Platform['tier'] = 'planning'): Platform => ({
  id, name: id, class: cls, use: '', hitch, tier, pivot: false, reverse: true, ground: 'tyre', wheels: 4, length_m: sourced(3), width_m: sourced(2), track_m: sourced(1.5),
  mass_kg: sourced(1000), speed_work_max_kmh: sourced(10), speed_transport_kmh: sourced(20), min_turn_radius_m: sourced(4),
});
const implement = (id: string, hitch: string, operations: string[], extra: Partial<Implement> = {}): Implement => ({ id, name: id, category: '植保', hitch, operations, width_m: sourced(6), length_m: sourced(1), mass_kg: sourced(100), ...extra });
const env = (id: string, ops: string[], platforms: string[], crops: string[] = []): Environment => ({ id, name: id, short: id, line_operations: ops, spatial: '', structure: 'swath', turn_space: 'open', slope_deg: 0, default_crops: crops, platforms });

const catalog: Catalog = {
  schema: 's', version: 'v',
  sources: [{ id: 'a', kind: 'standard', title: 'D497', checked: 'document', checked_on: '', scope: '' }, { id: 'b', kind: 'assumption', title: '假设', checked: 'search_summary', checked_on: '', scope: '' }],
  environments: [env('open_field', ['spray', 'till'], ['ackermann', 'diff_drive'], ['wheat']), env('barn', ['push'], ['diff_drive']), env('bare', [], ['diff_drive'])],
  crops: [{ id: 'wheat', name: '小麦', category: 'grain', stand: 'solid', yield_t_ha: 6, row_spacing_m: null, row_width_m: null, canopy_height_m: null }],
  operations: [
    { id: 'spray', name: '喷', family: 'line', stage: 'in_crop', resource: 'consume', default_rate: 150, objects: [], crop_protected: true },
    { id: 'till', name: '耕', family: 'line', stage: 'pre_crop', resource: 'none', objects: [], crop_protected: false },
    { id: 'push', name: '推', family: 'line', stage: 'any', resource: 'none', objects: [], crop_protected: false },
    { id: 'orphan', name: '无机具', family: 'line', stage: 'any', resource: 'none', objects: [], crop_protected: false },
  ],
  platforms: [platform('tractor', 'ackermann', ['3pt']), platform('robot', 'diff_drive', ['deck'], 'simulation'), platform('show', 'diff_drive', ['deck'], 'display')],
  implements: [implement('boom', '3pt', ['spray']), implement('plow', '3pt', ['till']), implement('pusher', 'deck', ['push']), implement('kit', 'integrated', ['spray'], { for: ['tractor'] })],
  labels: { tiers: {}, basis: { standard: '标准', assumption: '假设' }, platform_classes: {} },
};
const mission = (m: Partial<Mission>): Mission => ({ ...BASE, environment: 'open_field', crop: 'wheat', operation: 'spray', platform: 'tractor', implement: 'boom', overrides: {}, ...m });

describe('choices stay consistent', () => {
  it('lists only operations that belong to the environment and have a machine', () => {
    expect(lineOperations(catalog, catalog.environments[0]).map((o) => o.id)).toEqual(['spray', 'till']);
    expect(lineOperations(catalog, catalog.environments[1]).map((o) => o.id)).toEqual(['push']);
  });
  it('keeps what still fits', () => {
    const next = reconcile(catalog, mission({ overrides: { rate_per_ha: 90, work_width_m: 12 } }));
    expect(next).toMatchObject({ platform: 'tractor', implement: 'boom' });
    expect(next.overrides).toMatchObject({ rate_per_ha: 90, work_width_m: 12 });
  });
  it('moves dependent choices when the environment changes, and resets values that belonged to the old implement', () => {
    const next = reconcile(catalog, mission({ environment: 'barn', overrides: { work_width_m: 12 } }));
    expect(next).toMatchObject({ operation: 'push', platform: 'robot', implement: 'pusher', crop: null });
    expect(next.overrides.work_width_m).toBeNull();
  });
  it('fills the default rate for a consumed resource and clears it otherwise', () => {
    expect(reconcile(catalog, mission({})).overrides.rate_per_ha).toBe(150);
    expect(reconcile(catalog, mission({ operation: 'till', implement: 'plow', overrides: { rate_per_ha: 90 } })).overrides.rate_per_ha).toBeNull();
  });
  it('does not touch a mission in an environment without line work', () => {
    const m = mission({ environment: 'bare' });
    expect(reconcile(catalog, m)).toBe(m);
  });
  it('a dedicated implement fits only its own platform', () => {
    const [tractor, robot] = catalog.platforms;
    expect(implementFits(catalog.implements[3], { ...tractor, hitch: ['integrated'] }, 'spray')).toBe(true);
    expect(implementFits(catalog.implements[3], { ...robot, hitch: ['integrated'] }, 'spray')).toBe(false);
  });
  it('says why a platform does not qualify, and lists usable ones first', () => {
    const list = platformsFor(catalog, catalog.environments[0], 'spray');
    expect(list[0]).toMatchObject({ ok: true, platform: { id: 'tractor' } });
    expect(list.find((x) => x.platform.id === 'show')?.why).toBe('只有展示模型');
    expect(list.find((x) => x.platform.id === 'robot')?.why).toBe('没有可挂机具');
  });
});

describe('coverage matrix', () => {
  it('needs the operation in the environment and a usable rig', () => {
    expect(cell(catalog, 'open_field', 'spray')).toBe('native');
    expect(cell(catalog, 'barn', 'spray')).toBe('none');
    expect(cell(catalog, 'bare', 'spray')).toBe('none');
  });
});

describe('plan verdict', () => {
  it('states the saving, a loss, and the case where order does not matter', () => {
    expect(planVerdict(false, .29, .29)).toBe('非作业里程比顺序往复少 29%');
    expect(planVerdict(false, -.08, .1)).toBe('非作业里程比顺序往复多 8%');
    expect(planVerdict(false, 0, 0)).toBe('与顺序往复相同');
    expect(planVerdict(true, null, 0)).toBe('排行序没有收益，顺序往复已是最好');
    expect(planVerdict(true, null, .2)).toBe('最好的方案可少 20% 非作业里程');
    expect(planVerdict(true, null, null)).toBeNull();
  });
});

describe('display', () => {
  it('names the evidence level and marks summary-level sources', () => {
    expect(basisOf(catalog, { s: 'a' })).toMatchObject({ kind: 'standard', label: '标准' });
    expect(basisOf(catalog, { s: 'b' }).title).toContain('检索摘要');
  });
  it('formats absent values as a dash and short jobs in minutes', () => {
    expect(fmt(null)).toBe('—');
    expect(hours(.5)).toBe('30 分钟');
    expect(hours(2.25)).toBe('2.3 小时');
  });
});
