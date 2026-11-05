import type { Reference } from './types';

/** Short citation: family names and year. */
export function cite(r: Reference) {
  const fam = r.authors.map((a) => (a.includes(',') ? a.split(',')[0] : a.split(' ').slice(-1)[0]));
  const who = fam.length === 0 ? (r.venue ?? r.key) : fam.length === 1 ? fam[0] : fam.length === 2 ? `${fam[0]} & ${fam[1]}` : `${fam[0]} 等`;
  return `${who} (${r.year ?? 'n.d.'})`;
}
