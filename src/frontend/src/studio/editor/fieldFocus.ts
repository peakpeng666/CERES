import type { FormField } from './validation';

/**
 * Ask the inspector to open its fleet tab (InspectorPanel listens for `studio:focus-field`), then scroll to and
 * focus the input carrying `data-field`. The tab renders on the next frame, so the lookup retries briefly.
 * `onDone(false)` when the field never appears (e.g. no inspector on screen).
 */
export function focusFormField(f: FormField, onDone?: (ok: boolean) => void, root: ParentNode = document, tries = 10): void {
  window.dispatchEvent(new CustomEvent('studio:focus-field', { detail: f }));
  const attempt = (left: number) => {
    const input = findField(f.field, root);
    if (input) {
      input.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
      input.focus({ preventScroll: true });
      input.select?.();
      onDone?.(true);
    } else if (left > 0) requestAnimationFrame(() => attempt(left - 1));
    else onDone?.(false);
  };
  attempt(tries);
}

export function findField(field: string, root: ParentNode = document): HTMLInputElement | null {
  const esc = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(field) : field.replace(/["\\]/g, '\\$&');
  return root.querySelector<HTMLInputElement>(`input[data-field="${esc}"]`);
}
