import { describe, expect, it } from 'vitest';
import { getPattern, patternStatus } from './patterns';
import { defaultValues } from './catalog';
import { buildRowWeeding } from './patterns/rowWeeding';

const p = getPattern('row-weeding-coverage')!;
const base = defaultValues(p);
describe('行间机械除草', () => {
  it('是近似模式并显式声明代理', () => {
    expect(patternStatus(p)).toBe('approx');
    expect(p.serviceObject).toBe('row_set'); expect(p.proxyModel?.serviceObject).toBe('area');
    expect(p.approximation).toMatch('相位');
  });
  it('幅宽由行距和每趟行数推导，行向锁定为扫描方向', () => {
    const b = buildRowWeeding({ ...base, row_spacing: 0.75, rows_per_pass: 8, row_angle_deg: 30 }, 7);
    expect(b.instance.swath_width).toBe(6); expect(b.instance.angle_deg).toBe(30);
    expect(b.provenance.seed).toBe(7);
  });
  it('同一种子同一参数结果一致（除时间戳 id 外）', () => {
    expect(buildRowWeeding(base, 3).instance).toEqual(buildRowWeeding(base, 3).instance);
  });
  it('拒绝压行或无法掉头的组合，不静默修改参数', () => {
    expect(() => buildRowWeeding({ ...base, implement_width: 5 }, 1)).toThrow('压行');
    expect(() => buildRowWeeding({ ...base, headland_width: 3 }, 1)).toThrow('转弯半径');
    expect(() => buildRowWeeding({ ...base, rows_per_pass: 1, row_spacing: 0.3 }, 1)).toThrow('0.5–12');
    expect(() => buildRowWeeding({ ...base, rows_per_pass: 2.5 }, 1)).toThrow('整数');
    expect(() => buildRowWeeding({ ...base, field_w: NaN }, 1)).toThrow();
  });
});
