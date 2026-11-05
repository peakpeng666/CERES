/** Idempotent scenario creates on top of the existing Lab endpoints and lab/pendingWrites. */
import { lab, LabApiError } from '../lab/api';
import type { Scenario } from '../lab/types';
import { studio } from './api';
import type { FleetScenario } from './types';
import { clientScenarioId, containsSubmittedValue, contentDigest, createOnce, forgetWrite, importScenarioOnce as importScenarioOnceWith, mismatch, pendingWrite } from '../lab/pendingWrites';

type ForkBody = { instance?: unknown; title?: string };

/** Fork / draft copy: the client chooses the new id, so a retry can ask for it first. */
export function forkScenarioOnce(sourceId: string, body: ForkBody = {}, kind: 'fork' | 'draft-copy' = 'fork'): Promise<Scenario> {
  return createOnce<Scenario>({
    key: `${kind}:${sourceId}:${contentDigest(body)}`,
    kind,
    id: () => clientScenarioId(sourceId),
    send: (id) => studio.forkScenario(sourceId, { ...body, client_id: id }),
    recover: async (id) => {
      const found = await lab.scenario(id);
      const forkedFrom = (found.provenance as { forked_from?: string } | undefined)?.forked_from;
      if (found.id !== id || forkedFrom !== sourceId
        || (body.instance !== undefined && !containsSubmittedValue(found.instance, body.instance))) throw mismatch(id);
      return found;
    },
    // An older gateway ignores client_id and names the copy itself; that write is still single.
    acceptForeignId: true,
  });
}

/** Scenario JSON import: the id is part of the submitted document. */
export function importScenarioOnce(obj: unknown): Promise<Scenario> {
  return importScenarioOnceWith(obj, (doc) => studio.importScenario(doc));
}

export interface ImportCandidate { id: string; title: string }

/**
 * The GeoJSON endpoint names the scenario itself and records no source hash, so after an uncertain
 * outcome nothing identifies the write safely. The caller must choose: adopt one listed candidate or
 * confirm a re-import. Nothing is claimed or re-created automatically.
 */
export class UncertainImportError extends Error {
  constructor(readonly key: string, readonly candidates: ImportCandidate[]) {
    super(candidates.length
      ? `上次 GeoJSON 导入结果不确定：找到 ${candidates.length} 个可能的已保存场景（${candidates.map((c) => c.id).join('、')}），请选择其一，或确认未保存后重新导入`
      : '上次 GeoJSON 导入结果不确定，场景列表中没有可确认的同名场景；请确认未保存后再重新导入');
    this.name = 'UncertainImportError';
  }
}

export const geojsonImportKey = (geojson: unknown, title?: string) => `geojson:${contentDigest({ geojson, title: title ?? null })}`;

export type GeojsonResolution = { adopt: string } | { reimport: true };

export async function importGeojsonOnce(geojson: unknown, title?: string, resolution?: GeojsonResolution): Promise<FleetScenario> {
  const key = geojsonImportKey(geojson, title);
  if (resolution && 'adopt' in resolution) {
    const found = (await lab.scenario(resolution.adopt)) as unknown as FleetScenario;
    if ((found as { origin?: string }).origin !== 'user') throw new LabApiError('只能选择“我的场景”中的记录', 'invalid_choice', 400);
    forgetWrite(key);
    return found;
  }
  if (resolution && 'reimport' in resolution) forgetWrite(key);
  return createOnce<FleetScenario>({
    key,
    kind: 'geojson',
    id: () => clientScenarioId('geojson'),
    send: () => studio.importGeojson(geojson, title),
    recover: async () => {
      const { scenarios } = await lab.scenarios();
      const candidates = scenarios.filter((x) => (x as { origin?: string }).origin === 'user' && x.title === title)
        .map((x) => ({ id: x.id, title: x.title }));
      throw new UncertainImportError(key, candidates);
    },
    acceptForeignId: true,
  });
}

/** True while a GeoJSON import with this content still awaits the user's decision. */
export const geojsonImportPending = (geojson: unknown, title?: string) => !!pendingWrite(geojsonImportKey(geojson, title));
