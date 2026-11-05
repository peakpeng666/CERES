import { useEffect, useState } from 'react';
import { lab, useLabResource } from '../lab/api';
import type { DataPack } from '../lab/types';
import { Spinner } from '../lab/ui';
import { studio } from '../studio/api';
import type { SimBackendStatus, UpdateState } from '../desktop';
import { n0 } from './format';
import { Kvs, Row, Rows } from './ui';
import s from './desk.module.css';

const size = (bytes?: number | null) => (bytes == null ? '—' : bytes >= 1 << 30 ? `${(bytes / (1 << 30)).toFixed(1)} GB` : `${n0(bytes / (1 << 20))} MB`);

function PackRow({ p, reload }: { p: DataPack; reload: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const act = (fn: () => Promise<unknown>) => { setError(null); fn().then(reload).catch((e) => setError((e as Error).message)); };
  const busy = p.state === 'downloading' || p.state === 'installing';
  return (
    <>
      <Row wrap title={p.name} sub={p.state === 'installing' ? '校验并解压' : p.state === 'downloading' ? `下载中 ${n0((p.progress ?? 0) * 100)}%` : `${p.summary} · 下载 ${size(p.bytes)}，占用 ${size(p.unpacked_bytes)}`}
        value={busy ? <button type="button" className={s.go} onClick={() => act(() => lab.packCancel(p.id))}>取消</button>
          : p.state === 'installed' ? <button type="button" className={s.go} onClick={() => act(() => lab.packRemove(p.id))}>删除</button>
            : <button type="button" className={s.go} onClick={() => act(() => lab.packInstall(p.id))}>{p.state === 'failed' ? '重试' : '下载'}</button>} />
      {(error ?? p.error) && <p className={s.docBad}>{error ?? p.error}</p>}
    </>
  );
}

/** Data that does not ship with the app. Each source says what it holds and what it costs. */
export function DataPage() {
  const packs = useLabResource('packs', () => lab.packs(), { reloadOn: 'pack', pollMs: 2000 });
  const library = useLabResource('library-overview', () => studio.library());
  const cvrp = library.data?.cvrplib as Record<string, number | string> | undefined;
  const fields = library.data?.fields as { downloaded?: boolean; fields?: number } | undefined;
  return (
    <div className={s.page} style={{ height: '100%' }}>
      <div className={s.pageIn}>
        <h2>数据包</h2>
        <p className={s.noteLine}>这些数据不随安装包提供，需要时再下载。下载后会先校验，再放进应用的数据目录。</p>
        {packs.error && <p className={s.docBad}>{packs.error.message}</p>}
        {!packs.data && !packs.error && <Spinner />}
        <Rows>{packs.data?.packs.map((p) => <PackRow key={p.id} p={p} reload={packs.reload} />)}</Rows>
        {packs.data && !packs.data.packs.length && <p className={s.emptyLine}>这个版本没有可下载的数据包。</p>}
        <h2>基准资料</h2>
        <p className={s.noteLine}>打开时从原始站点按需下载并缓存。</p>
        <Rows>
          <Row title="CVRPLIB 算例" sub={cvrp ? `${cvrp.instances} 个算例，已缓存 ${cvrp.cached} 个` : undefined} onClick={() => { location.hash = '#/library/cvrplib'; }} />
          <Row title="真实田块" sub={fields?.downloaded ? `Fields2Benchmark，${fields.fields} 块` : 'Fields2Benchmark，首次使用时下载'} onClick={() => { location.hash = '#/library/fields'; }} />
        </Rows>
      </div>
    </div>
  );
}

const UPDATE: Record<UpdateState['status'], string> = { unavailable: '开发版本不检查更新', idle: '尚未检查', checking: '正在检查', current: '已是最新版本', downloading: '正在下载更新', ready: '更新已下载', error: '检查失败' };

function SimBackend({ current }: { current: 'native' | 'container' }) {
  const [st, setSt] = useState<SimBackendStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void window.desktop!.sim.status().then(setSt).catch((e) => setError(String(e))); }, []);
  if (!st) return error ? <p className={s.docBad}>{error}</p> : <Spinner />;
  const ready = st.docker.ready && st.compose.ready;
  const toggle = () => {
    setBusy(true); setError(null);
    (current === 'container' ? window.desktop!.sim.disable() : window.desktop!.sim.enable()).catch((e) => { setError(String(e)); setBusy(false); });
  };
  return (
    <>
      <p className={s.noteLine}>Gazebo 和 ROS 2 只能在 Linux 上运行。容器后端把它们连同完整的工作台放进一个镜像，Fields2Cover 和 CVRP 数值任务也在里面。算法库和 MuJoCo 物理预演不需要它。</p>
      <div style={{ paddingTop: 8 }}>
        <Kvs items={[['Docker', st.docker.ready ? `可用 ${st.docker.version}` : st.docker.detail ?? '没有找到'], ['Compose', st.compose.ready ? `可用 ${st.compose.version}` : '没有找到'],
          !!st.wsl && ['WSL2', st.wsl.ready ? '可用' : '未启用'], ['镜像', st.imagePresent ? `已下载 ${size(st.imageBytes)}` : `首次开启时下载，约 10 GB`]]} />
      </div>
      {!ready && <p className={s.noteLine}>需要先安装 Docker{st.platform === 'win32' ? ' Desktop（WSL2 后端）' : ''}。<a href={st.download} target="_blank" rel="noreferrer">安装说明</a></p>}
      <div className={s.nextRow} style={{ paddingTop: 10 }}>
        <button type="button" className={`${s.btn} ${current === 'native' ? s.btnPrimary : ''}`} disabled={busy || (!ready && current === 'native')} onClick={toggle}>{busy ? '切换中' : current === 'container' ? '回到内置后端' : '开启容器后端'}</button>
      </div>
      {error && <p className={s.docBad}>{error}</p>}
    </>
  );
}

/** Things only the installed app has: its version, updates and where its simulator comes from. */
export function DesktopPage() {
  const [info, setInfo] = useState<Awaited<ReturnType<NonNullable<typeof window.desktop>['info']>> | null>(null);
  const [update, setUpdate] = useState<UpdateState | null>(null);
  useEffect(() => {
    void window.desktop?.info().then(setInfo);
    void window.desktop?.update.state().then(setUpdate);
    return window.desktop?.update.onChange(setUpdate);
  }, []);
  if (!window.desktop) return <p className={s.emptyLine}>这一页只在桌面应用里有。</p>;
  if (!info || !update) return <div className={s.pageIn}><Spinner /></div>;
  return (
    <div className={s.page} style={{ height: '100%' }}>
      <div className={s.pageIn}>
        <h2>版本</h2>
        <div style={{ paddingTop: 6 }}><Kvs items={[['应用', info.version], ['后端', info.backend === 'container' ? '容器' : '内置'], ['状态', `${UPDATE[update.status]}${update.status === 'downloading' && update.percent != null ? ` ${update.percent}%` : ''}${update.version ? ` · ${update.version}` : ''}`], !!update.detail && ['原因', update.detail]]} /></div>
        <div className={s.nextRow} style={{ paddingTop: 10 }}>
          {update.status === 'ready'
            ? <button type="button" className={`${s.btn} ${s.btnPrimary}`} onClick={() => void window.desktop!.update.install()}>重启并更新</button>
            : <button type="button" className={s.btn} disabled={update.status === 'unavailable' || update.status === 'checking' || update.status === 'downloading'} onClick={() => void window.desktop!.update.check()}>检查更新</button>}
          <button type="button" className={s.btn} onClick={() => void window.desktop!.openLogs()}>打开日志目录</button>
        </div>
        <h2>仿真后端</h2>
        <SimBackend current={info.backend} />
      </div>
    </div>
  );
}
