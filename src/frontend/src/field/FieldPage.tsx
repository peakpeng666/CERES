import type { Route } from '../app/router';
import { PageHead } from '../desk/Surface';
import { Coverage } from './Coverage';
import { Design } from './Design';
import { Machines } from './Machines';
import d from '../desk/desk.module.css';

/** The agricultural side of Lab: design a job, look up machines, see what the system covers. */
export function FieldPage({ route }: { route: Route }) {
  return (
    <div className={d.main} style={{ flex: 1 }}>
      <PageHead page="field" route={route} />
      {route.id === 'machines' ? <Machines /> : route.id === 'coverage' ? <Coverage /> : <Design />}
    </div>
  );
}
