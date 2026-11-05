import { lazy, Suspense } from 'react';
import { useAssets } from './assets/store';

const ImportWizard = lazy(() => import('./assets/ImportWizard').then((m) => ({ default: m.ImportWizard })));
const MapToTaskMap = lazy(() => import('./assets/MapToTaskMap').then((m) => ({ default: m.MapToTaskMap })));

/** Import wizard and map → task-map wizard: reachable from every workspace. */
export function AssetModals() {
  const importOpen = useAssets((x) => x.importOpen);
  const taskMapFrom = useAssets((x) => x.taskMapFrom);
  return (
    <Suspense fallback={null}>
      {importOpen && <ImportWizard />}
      {taskMapFrom && <MapToTaskMap />}
    </Suspense>
  );
}
