import { useEffect, useRef, useState } from 'react';
import { lab, useLabResource } from '../lab/api';
import type { ScenarioSummary } from '../lab/types';
import type { Catalog, Mission, Preview } from './types';

export const useCatalog = () => useLabResource('agri-catalog', () => lab.agriCatalog());

/** The compiled mission for the current inputs. The last good result stays on screen while the next one is on its way. */
export function usePreview(mission: Mission | null) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ticket = useRef(0);
  const key = mission ? JSON.stringify(mission) : '';
  useEffect(() => {
    if (!mission) return;
    const mine = ++ticket.current;
    setBusy(true);
    const timer = setTimeout(() => {
      lab.agriCompile(mission).then((value) => {
        if (mine !== ticket.current) return;
        setPreview(value); setError(null); setBusy(false);
      }).catch((e: Error) => {
        if (mine !== ticket.current) return;
        setError(e.message); setBusy(false);
      });
    }, 160);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return { preview, error, busy };
}

export const createMission = (mission: Mission): Promise<ScenarioSummary> => lab.agriCreate(mission);
export type { Catalog };
