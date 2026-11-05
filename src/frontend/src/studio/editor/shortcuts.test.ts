import { describe, expect, it } from 'vitest';
import { isBoxSelectShortcut } from './shortcuts';

const key = (over: Partial<Parameters<typeof isBoxSelectShortcut>[0]> = {}) => ({ key: 'b', metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, repeat: false, target: null, ...over });
const el = (typing: boolean, editable = false) => ({ closest: (s: string) => (typing && s.includes('input') ? {} : null), isContentEditable: editable }) as unknown as EventTarget;

describe('box-select shortcut', () => {
  it('fires on plain B', () => {
    expect(isBoxSelectShortcut(key())).toBe(true);
    expect(isBoxSelectShortcut(key({ key: 'B' }))).toBe(true);
    expect(isBoxSelectShortcut(key({ target: el(false) }))).toBe(true);
  });
  it('ignores modifiers, repeats, other keys and typing targets', () => {
    for (const m of ['metaKey', 'ctrlKey', 'altKey', 'shiftKey', 'repeat'] as const) expect(isBoxSelectShortcut(key({ [m]: true }))).toBe(false);
    expect(isBoxSelectShortcut(key({ key: 'v' }))).toBe(false);
    expect(isBoxSelectShortcut(key({ target: el(true) }))).toBe(false);
    expect(isBoxSelectShortcut(key({ target: el(false, true) }))).toBe(false);
  });
});
