import { flushSync } from 'react-dom';

/** The app's motion setting and the system's. Either one turns the choreography off; state changes still happen. */
export function motionOff(): boolean {
  if (typeof document === 'undefined') return true;
  if (document.documentElement.dataset.motion === 'reduced') return true;
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

let latest: ViewTransition | null = null;

/** A transition overtaken by the next one is normal, so its rejected promises are nobody's error, and only the last one clears the mark. */
function follow(change: ViewTransition) {
  latest = change;
  change.ready.catch(() => undefined);
  void change.finished.catch(() => undefined).finally(() => {
    if (latest === change) delete document.documentElement.dataset.vt;
  });
}

/** A change of page: the old page lifts away while the new one settles, and an element that exists on both travels. */
export function pageChange(update: () => void) {
  if (motionOff() || document.hidden || !document.startViewTransition) {
    update();
    return;
  }
  document.documentElement.dataset.vt = 'page';   // the elements that travel are named only meanwhile, so nothing else changes stacking
  follow(document.startViewTransition(() => flushSync(update)));
}

/** Switching themes opens the new look outward from where the click landed. */
export function themeReveal(apply: () => void, x: number, y: number) {
  if (motionOff() || !document.startViewTransition) {
    apply();
    return;
  }
  const root = document.documentElement;
  root.dataset.vt = 'theme';
  const change = document.startViewTransition(() => flushSync(apply));
  follow(change);
  void change.ready.then(() => {
    const reach = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    root.animate({ clipPath: [`circle(0 at ${x}px ${y}px)`, `circle(${reach}px at ${x}px ${y}px)`] },
      { duration: 620, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', pseudoElement: '::view-transition-new(root)' });
  }).catch(() => undefined);
}
