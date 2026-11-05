/** B = box-select tool. Inventory of studio keys (Studio.tsx useShortcuts): V H T O P D U C F Esc Del/Backspace, ⌘/Ctrl+K/Enter/Z/S; replay uses Space/←/→/Home/End. */
export const BOX_SELECT_KEY = 'b';

type KeyLike = Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'repeat'> & { target: EventTarget | null };

const TYPING = 'input, textarea, select, [contenteditable="true"], [contenteditable=""], .cm-editor';

export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as { closest?: (s: string) => unknown; isContentEditable?: boolean } | null;
  return !!el && (!!el.isContentEditable || !!el.closest?.(TYPING));
}

/** Plain B only: no modifier (⌘B/Ctrl+B/Alt+B/Shift+B stay free for the browser and the OS), no auto-repeat, not while typing. */
export function isBoxSelectShortcut(e: KeyLike): boolean {
  return e.key.toLowerCase() === BOX_SELECT_KEY && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.repeat && !isTypingTarget(e.target);
}
