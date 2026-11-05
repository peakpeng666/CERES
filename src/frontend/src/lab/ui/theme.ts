import { useEffect, useState } from 'react';

/** Canvas and WebGL views cannot read CSS variables, so they take the resolved value and follow theme changes. */
export const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export function useThemeKey(): string {
  const read = () => document.documentElement.getAttribute('data-theme') ?? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const [key, setKey] = useState(read);
  useEffect(() => {
    const on = () => setKey(read());
    const media = matchMedia('(prefers-color-scheme: dark)');
    media.addEventListener('change', on);
    const watch = new MutationObserver(on);
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => { media.removeEventListener('change', on); watch.disconnect(); };
  }, []);
  return key;
}

export function useCssVars<K extends string>(names: Record<K, string>): Record<K, string> {
  const key = useThemeKey();
  const [values, setValues] = useState(() => resolve(names));
  useEffect(() => { setValues(resolve(names)); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return values;
}
function resolve<K extends string>(names: Record<K, string>) {
  return Object.fromEntries(Object.entries(names).map(([k, v]) => [k, cssVar(v as string)])) as Record<K, string>;
}
