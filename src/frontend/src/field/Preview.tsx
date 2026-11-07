import { useMemo } from 'react';
import type { Preview, Segment } from './types';
import s from './field.module.css';

const PAD = 10;

/** The field to scale: boundary, headland, work lines and the rig. Fixed aspect so a change of field reads as a change. */
export function FieldPreview({ preview, segments }: { preview: Preview; segments?: Segment[][] }) {
  const geo = useMemo(() => {
    const ring = preview.field;
    if (!ring?.length) return null;
    const xs = ring.map((p) => p[0]);
    const ys = ring.map((p) => p[1]);
    const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
    const w = maxx - minx, h = maxy - miny;
    const margin = Math.max(w, h) * PAD / 200;
    return { minx: minx - margin, maxy: maxy + margin, w: w + 2 * margin, h: h + 2 * margin, stroke: Math.max(w, h) / 500 };
  }, [preview.field]);
  if (!geo || !preview.field) return null;
  const px = (p: number[]) => `${p[0] - geo.minx},${geo.maxy - p[1]}`;
  const ring = preview.field.map(px).join(' ');
  const rig = preview.rig;
  const pitch = preview.pitch_m ?? 1;
  const st = preview.stations?.[0];
  return (
    <svg className={s.previewSvg} viewBox={`0 0 ${geo.w} ${geo.h}`} role="img" aria-label="田块预览" preserveAspectRatio="xMidYMid meet">
      <polygon points={ring} className={s.pvField} strokeWidth={geo.stroke} />
      {preview.obstacles?.map((o, i) => <polygon key={i} points={o.map(px).join(' ')} className={s.pvObstacle} strokeWidth={geo.stroke} />)}
      {preview.lines.map((l) => {
        const a = px(l.p1).split(','), b = px(l.p2).split(',');
        return <line key={l.id} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className={s.pvSwath} strokeWidth={Math.max(pitch * .9, geo.stroke)} />;
      })}
      {preview.lines.map((l) => {
        const a = px(l.p1).split(','), b = px(l.p2).split(',');
        return <line key={`c${l.id}`} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className={s.pvLine} strokeWidth={geo.stroke} />;
      })}
      {segments?.flat().filter((x) => x.kind !== 'work' && x.polyline.length > 1).map((x, i) => (
        <polyline key={i} points={x.polyline.map(px).join(' ')} className={x.kind === 'turn' ? s.pvTurn : s.pvTransfer} strokeWidth={geo.stroke * 1.4} />
      ))}
      {st && <circle cx={st.xy[0] - geo.minx} cy={geo.maxy - st.xy[1]} r={Math.max(geo.stroke * 4, rig?.clearance_width_m ?? 0)} className={s.pvStation} />}
    </svg>
  );
}
