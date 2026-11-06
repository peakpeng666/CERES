import s from './desk.module.css';
/** Shell icons: one hand-drawn 16px set, 1.4px stroke, used only by the desk chrome. */
const PATHS = {
  plus: 'M8 3v10M3 8h10',
  search: 'M10.6 10.6 14 14M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z',
  route: 'M3.5 12.5h4.2a2 2 0 0 0 2-2V5.5a2 2 0 0 1 2-2h1M2 12.5a1.5 1.5 0 1 0 3 0 1.5 1.5 0 0 0-3 0',
  box: 'M8 2l5.5 3v6L8 14l-5.5-3V5zM2.5 5 8 8l5.5-3M8 8v6',
  flask: 'M6 2h4M6.6 2v4L3 12.3A1.1 1.1 0 0 0 4 14h8a1.1 1.1 0 0 0 1-1.7L9.4 6V2M5 10h6',
  rover: 'M3 10.5V7.8A1.8 1.8 0 0 1 4.8 6h6.4A1.8 1.8 0 0 1 13 7.8v2.7M2 10.5h12M5 12.8a1.3 1.3 0 1 0 0-2.6 1.3 1.3 0 0 0 0 2.6ZM11 12.8a1.3 1.3 0 1 0 0-2.6 1.3 1.3 0 0 0 0 2.6ZM8 6V3.5M6.5 3.5h3',
  book: 'M8 4C6.5 3 4.5 3 2.5 3.5v9c2-.5 4-.5 5.5.5 1.5-1 3.5-1 5.5-.5v-9C11.5 3 9.5 3 8 4zM8 4v9',
  server: 'M3.5 3h9a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM3.5 9h9a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-2a1 1 0 0 1 1-1zM5 5h.01M5 11h.01',
  side: 'M3.5 3h9a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5v-7A1.5 1.5 0 0 1 3.5 3zM6 3v10',
  pane: 'M3.5 3h9a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5v-7A1.5 1.5 0 0 1 3.5 3zM10 3v10',
  widen: 'M6 3H3v3M10 3h3v3M6 13H3v-3M10 13h3v-3',
  close: 'M4 4l8 8M12 4l-8 8',
  right: 'M6 3.5 10.5 8 6 12.5',
  left: 'M10 3.5 5.5 8 10 12.5',
  up: 'M8 13V3.5M4 7.5l4-4 4 4',
  down: 'M3.5 6 8 10.5 12.5 6',
  check: 'M3 8.5 6.5 12 13 4.5',
  clock: 'M8 4.5V8l2.2 1.6M13.5 8a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0Z',
  ring: 'M13.5 8a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0Z',
  cross: 'M4.5 4.5l7 7M11.5 4.5l-7 7',
  stop: 'M4.5 4.5h7v7h-7z',
  pause: 'M5.5 3.5v9M10.5 3.5v9',
  play: 'M5 3.5 12 8l-7 4.5z',
  dots: 'M3.5 8h.01M8 8h.01M12.5 8h.01',
  grid: 'M3 3h4v4H3zM9 3h4v4H9zM3 9h4v4H3zM9 9h4v4H9z',
  numbers: 'M3 13V6M7 13V3M11 13V8M14 13H2',
  history: 'M2.5 8a5.5 5.5 0 1 0 1.8-4M2.5 3v3h3M8 5v3l2 1.5',
  gear: 'M8 10.2a2.2 2.2 0 1 0 0-4.4 2.2 2.2 0 0 0 0 4.4ZM8 2v1.6M8 12.4V14M2 8h1.6M12.4 8H14M3.8 3.8l1.1 1.1M11.1 11.1l1.1 1.1M3.8 12.2l1.1-1.1M11.1 4.9l1.1-1.1',
  pin: 'M6 2.5h4l-.5 4 2 2v1h-7v-1l2-2zM8 9.5V14',
  download: 'M8 2.5v8M4.5 7 8 10.5 11.5 7M3 13.5h10',
  edit: 'M3 13l.6-2.6 7-7a1.3 1.3 0 0 1 1.9 1.9l-7 7zM9.6 4.4l2 2',
  archive: 'M2.5 3.5h11v3h-11zM3.5 6.5v6h9v-6M6.5 9h3',
  map: 'M2.5 4l3.7-1.5 3.6 1.5 3.7-1.5v9.5l-3.7 1.5-3.6-1.5L2.5 13.5zM6.2 2.5V12M9.8 4v9.5',
} as const;
export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.4}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={PATHS[name]} />
    </svg>
  );
}

/** The product mark: the character 田 with a depot in one plot. It draws itself once, when the app opens. */
export function Mark({ size = 20 }: { size?: number }) {
  return (
    <svg className={s.mark} width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5} aria-hidden>
      <rect x={2} y={2} width={16} height={16} rx={3.5} pathLength={1} />
      <path d="M10 2v16M2 10h16" pathLength={1} />
      <circle cx={6} cy={14} r={1.7} fill="currentColor" stroke="none" />
    </svg>
  );
}
