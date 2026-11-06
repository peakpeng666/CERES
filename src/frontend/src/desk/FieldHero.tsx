import { useEffect, useMemo, useRef, type CSSProperties } from 'react';
import { motionOff } from '../lib/motion';
import s from './desk.module.css';

const W = 1440, H = 900;
const ROWS = 16;

/** Crop rows that sweep across the field. The curve is the same for every row, so they read as one planted surface. */
function row(i: number) {
  const y = -70 + i * (H + 140) / (ROWS - 1);
  const lift = 150 - i * 7;
  return `M-60 ${y + 120} C ${W * 0.3} ${y - lift}, ${W * 0.62} ${y + lift * 0.9}, ${W + 60} ${y - 80}`;
}

/** One vehicle's route: out along a headland, up and down the rows, and home. */
const ROUTE = 'M92 806 C 210 782, 300 770, 420 742 S 702 706, 884 664 S 1214 630, 1318 566 C 1376 526, 1352 470, 1262 470 '
  + 'C 1090 476, 930 498, 760 516 S 420 556, 256 556 C 158 552, 132 480, 214 448 C 372 398, 560 372, 742 338 S 1094 286, 1226 210';
const STOPS = [0.13, 0.27, 0.41, 0.55, 0.68, 0.84, 0.97];

/**
 * The new-task page's background: a field that plants itself, then a route that drives across it and stops.
 * It runs once and leaves a still picture, so an idle window costs nothing.
 */
export function FieldHero() {
  const line = useRef<SVGPathElement>(null);
  const head = useRef<SVGCircleElement>(null);
  const rows = useMemo(() => Array.from({ length: ROWS }, (_, i) => row(i)), []);
  const stops = useRef<(SVGCircleElement | null)[]>([]);
  useEffect(() => {
    const path = line.current, dot = head.current;
    if (!path || !dot) return;
    const total = path.getTotalLength();
    const spots = STOPS.map((f) => path.getPointAtLength(total * f));
    stops.current.forEach((c, i) => { if (c) { c.setAttribute('cx', String(spots[i].x)); c.setAttribute('cy', String(spots[i].y)); } });
    path.style.strokeDasharray = `${total} ${total}`;
    if (motionOff()) {
      path.style.strokeDashoffset = '0';
      stops.current.forEach((c) => c?.setAttribute('data-on', ''));
      return;
    }
    path.style.strokeDashoffset = String(total);
    const start = performance.now() + 700, ms = 5200;
    let frame = 0;
    const step = (now: number) => {
      const p = Math.max(0, Math.min(1, (now - start) / ms));
      const e = p < 0.5 ? 2 * p * p : 1 - (-2 * p + 2) ** 2 / 2;
      const at = path.getPointAtLength(total * e);
      path.style.strokeDashoffset = String(total * (1 - e));
      dot.setAttribute('cx', String(at.x));
      dot.setAttribute('cy', String(at.y));
      dot.dataset.on = p > 0 && p < 1 ? '' : 'done';
      STOPS.forEach((f, i) => { if (e >= f) stops.current[i]?.setAttribute('data-on', ''); });
      if (p < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, []);
  return (
    <div className={s.hero} aria-hidden>
      <i className={s.heroGlow} />
      <i className={s.heroGlow} data-b />
      <svg className={s.heroArt} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice">
        <g className={s.heroRows}>{rows.map((d, i) => <path key={i} d={d} pathLength={1} style={{ '--i': i } as CSSProperties} />)}</g>
        <path ref={line} className={s.heroRoute} d={ROUTE} />
        {STOPS.map((f, i) => <circle key={f} ref={(c) => { stops.current[i] = c; }} className={s.heroStop} r={4.5} />)}
        <circle ref={head} className={s.heroHead} r={6} />
      </svg>
    </div>
  );
}
