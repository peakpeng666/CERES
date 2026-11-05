#!/usr/bin/env python3
"""Isolated runner lifecycle. All child output is private, never echoed on failure."""
import argparse, ctypes, json, os, pathlib, re, signal, subprocess, sys, time
HINTS = frozenset({'none','source_identity','package_install','dependency_fetch','network','disk_full','native_build','registry_identity','unknown','apt_signature','apt_source_conflict','apt_release','apt_hook','apt_lock','apt_package_missing','apt_dependency_conflict','apt_libunwind_conflict','stage_timeout','cancelled'})
LIMIT = 65536

class StageDeadlineExceeded(TimeoutError):
    """The supervisor's stage budget expired, not an inferred APT error."""

class StageCancelled(Exception):
    """Explicit interruption is distinct from deadline expiry."""

def classify(path, exit_code):
    if exit_code == 0:
        return 'none'
    try:
        path = pathlib.Path(path)
        if path.is_symlink() or not path.is_file():
            return 'unknown'
        with path.open('rb') as stream:
            stream.seek(0,2)
            size=stream.tell()
            stream.seek(max(0,size-LIMIT))
            tail=stream.read(LIMIT).decode('utf-8',errors='replace').lower()
    except OSError:
        return 'unknown'
    # A successfully repaired attempt is historical evidence. Only inspect
    # output after the latest wrapper-owned attempt boundary in this tail.
    boundaries=list(re.finditer(r'^urp_install_attempt_start [12]\r?$',tail,re.MULTILINE))
    if boundaries:tail=tail[boundaries[-1].end():]
    # Require an explicit conflict edge, not mere mention of both packages.
    unwind=r'(?:libunwind-[0-9]{1,2}-dev[^\n]*conflicts(?::| on)[^\n]*libunwind-dev|libunwind-dev[^\n]*conflicts(?::| on)[^\n]*libunwind-[0-9]{1,2}-dev)'
    if re.search(unwind,tail):return 'apt_libunwind_conflict'
    markers = (
        ('disk_full', ('no space left on device','disk quota exceeded')),
        ('source_identity', ('source_revision_mismatch','index_revision_mismatch','tracked_blob_mismatch','tracked_file_missing','tracked_symlink_changed','unmerged_index','empty_source_checkout')),
        ('registry_identity', ('npm_mirror_identity_mismatch','non_allowlisted_npm_source')),
        ('apt_source_conflict', ('conflicting values set for option signed-by','conflicting values set for option trusted')),
        ('apt_signature', ('no_pubkey','expkeysig','badsig','the following signatures were invalid','the following signatures couldn','is not signed','at least one invalid signature was encountered')),
        ('apt_release', ('does not have a release file','no longer has a release file','release file is not valid yet','release file is expired')),
        ('apt_hook', ('problem executing scripts apt::update::post-invoke','problem executing scripts apt::update::pre-invoke','problem executing scripts dpkg::post-invoke','problem executing scripts dpkg::pre-invoke')),
        ('apt_lock', ('could not get lock','unable to acquire the dpkg frontend lock','unable to lock directory')),
        ('apt_package_missing', ('e: unable to locate package','has no installation candidate','unable to find a source package')),
        ('network', ('temporary failure in name resolution','could not resolve host','network is unreachable','connection timed out','connection reset by peer','certificate verify failed')),
        ('apt_dependency_conflict', ('following packages have unmet dependencies','unable to correct problems, you have held broken packages','e: unmet dependencies','pkgproblemresolver::resolve generated breaks')),
        ('package_install', ('e: unable to locate package','e: unmet dependencies','dpkg: error','sub-process /usr/bin/dpkg returned an error code')),
        ('dependency_fetch', ('failed to download','failed to fetch','no solution found when resolving dependencies','npm error code eintegrity','npm err! code eintegrity')),
        ('native_build', ('cmake error','ninja: build stopped','collect2: error: ld returned','fatal error:')),
    )
    for hint, needles in markers:
        if any(needle in tail for needle in needles):
            return hint
    return 'unknown'


def installation_step(path):
    try:
        with pathlib.Path(path).open('rb') as stream:
            stream.seek(0,2);stream.seek(max(0,stream.tell()-LIMIT))
            tail=stream.read(LIMIT).decode('utf-8',errors='replace')
    except OSError:return 'unknown'
    step='unknown'
    for line in tail.splitlines():
        if line == 'URP_INSTALL_PHASE registry_check':step='registry_check'
        elif line.startswith('URP_INSTALL_PHASE '):step='other'
        if not re.match(r'^\++URP_SETUP ',line):continue
        if re.search(r'\bapt-get update(?:\s|$)',line):step='apt_update'
        elif re.search(r'\bapt-get install(?:\s|$)',line):step='apt_install'
        elif re.search(r'\b(?:uv|npm|cmake|g\+\+|python3|curl)\s',line):step='other'
    return step


INSTALL_PHASES = frozenset({'unknown', 'source_identity', 'registry_check', 'setup',
    'apt', 'apt_update', 'apt_install', 'uv', 'node', 'npm', 'native', 'runtime_check', 'apt_diagnostic', 'apt_repair', 'complete'})

def installation_trace_state(path):
    """Fixed phase plus a private byte boundary identifying its traced command."""
    try:
        path=pathlib.Path(path)
        if path.is_symlink() or not path.is_file():return 'unknown',None
        with path.open('rb') as f:
            f.seek(0,2);start=max(0,f.tell()-LIMIT);f.seek(start);tail=f.read(LIMIT)
    except OSError:return 'unknown',None
    phase='unknown';boundary=None;offset=start
    for raw in tail.splitlines(keepends=True):
        line=raw.decode('utf-8',errors='replace').rstrip('\r\n')
        line_offset=offset;offset+=len(raw)
        # The bounded tail may start in the middle of an arbitrary trace line.
        if start and line_offset==start:continue
        candidate=None
        if line.startswith('URP_INSTALL_PHASE '):
            marker=line.removeprefix('URP_INSTALL_PHASE ')
            if marker in INSTALL_PHASES:candidate=marker
        elif re.match(r'^\++URP_SETUP ',line):
            if re.search(r'\bapt-get update(?:\s|$)',line):candidate='apt_update'
            elif re.search(r'\bapt-get install(?:\s|$)',line):candidate='apt_install'
            elif re.search(r'\bapt-get\s',line):candidate='apt'
            elif re.search(r'\buv\s|astral\.sh/uv/',line):candidate='uv'
            elif 'https://nodejs.org/dist/' in line:candidate='node'
            elif re.search(r'\bnpm\s',line):candidate='npm'
            elif 'src/lab/ros/native/install-ros-extras.sh' in line or 'deploy/build-cohort.sh' in line:candidate='native'
            elif re.search(r'\bpython3 deploy/(?:record-sim-profiles|check-runtime)\.py(?:\s|$)',line):candidate='runtime_check'
        if candidate is not None:phase=candidate;boundary=line_offset
    return phase,boundary


def installation_progress(path):
    """Reduce private control traces to enums; never return commands or paths."""
    return installation_trace_state(path)[0]


APT_EVENTS = (
    ('download', r'^Get:[0-9]+\s'),
    ('index_hit', r'^Hit:[0-9]+\s'),
    ('index_ignored', r'^Ign:[0-9]+\s'),
    ('index_error', r'^Err:[0-9]+\s'),
    ('unpack', r'^Unpacking '),
    ('configure', r'^Setting up '),
    ('triggers', r'^Processing triggers for '),
    ('reading_lists', r'^Reading package lists'),
    ('reading_state', r'^Reading state information'),
    ('resolving_dependencies', r'^Building dependency tree'),
    ('fetched', r'^Fetched [0-9]'),
    ('lock_wait', r'^Waiting for cache lock:'),
)

APT_TRANSPORT_SIGNALS = {
    'dns': ('could not resolve', 'temporary failure resolving'),
    'timeout': ('connection timed out', 'connection timeout', 'timed out waiting'),
    'connection_refused': ('connection refused',),
    'connection_reset': ('connection reset by peer',),
    'connection_failed': ('connection failed', 'could not connect'),
    'network_unreachable': ('network is unreachable',),
    'tls': ('certificate verification failed', 'certificate verify failed', 'could not handshake'),
    'hash_mismatch': ('hash sum mismatch', 'file has unexpected size'),
}
APT_HOST_GROUPS = {
    'packages.ros.org': 'ros',
    'packages.osrfoundation.org': 'gazebo',
    'archive.ubuntu.com': 'ubuntu',
    'azure.archive.ubuntu.com': 'ubuntu',
    'security.ubuntu.com': 'ubuntu',
}

class AptOutputObservation:
    """Lower-bound event counts AFTER phase detection, not package totals.

    Each sample reads at most LIMIT bytes. No commands, package names, repository
    names or original output are returned. Unknown/localized output stays unknown.
    """
    def __init__(self,path,now):
        self.path=pathlib.Path(path)
        self.offset=self.size()
        self.last_size=self.offset
        self.last_event='unknown'
        self.counts={name:0 for name,_ in APT_EVENTS}
        self.truncated=False
        self.last_activity=None
        self.transport={name:0 for name in (*APT_TRANSPORT_SIGNALS,'http_429','http_4xx','http_5xx')}
        self.hosts={name:0 for name in ('ros','gazebo','ubuntu','other')}

    def transport_line(self,line):
        """Count known output signals only; never emit hostnames or error text."""
        lowered=line.casefold()
        for name,needles in APT_TRANSPORT_SIGNALS.items():
            if any(needle in lowered for needle in needles):
                self.transport[name]=min(100000,self.transport[name]+1)
        if line[:1].isspace() or line.startswith('E: Failed to fetch '):
            status=re.search(r'(?:^|\s)([45][0-9]{2})\s+[A-Za-z]',line)
            if status:
                code=status.group(1)
                name='http_429' if code=='429' else ('http_5xx' if code.startswith('5') else 'http_4xx')
                self.transport[name]=min(100000,self.transport[name]+1)
        record=re.match(r'^(?:Get|Ign|Err):[0-9]+\s+https?://([^/\s]+)',line)
        if record:
            # Credentials, paths, query strings and unknown hosts stay private.
            authority=record.group(1).rsplit('@',1)[-1].split(':',1)[0].lower()
            group=APT_HOST_GROUPS.get(authority,'other')
            self.hosts[group]=min(100000,self.hosts[group]+1)

    def size(self):
        try:
            if self.path.is_symlink() or not self.path.is_file():return 0
            return self.path.stat().st_size
        except OSError:return 0

    def sample(self,now):
        try:
            if self.path.is_symlink() or not self.path.is_file():return
            with self.path.open('rb') as stream:
                stream.seek(0,2);size=stream.tell()
                if size!=self.last_size:
                    self.last_activity=now;self.last_size=size
                if size<self.offset:
                    # Log replacement/truncation breaks observation continuity.
                    self.truncated=True;self.offset=size;self.last_event='unknown'
                    return
                if size==self.offset:return
                start=max(self.offset,size-LIMIT)
                stream.seek(start);raw=stream.read(min(LIMIT,size-start))
                if start>self.offset:
                    self.truncated=True
                    # Never interpret the beginning of a truncated line.
                    split=raw.find(b'\n')
                    raw=raw[split+1:] if split>=0 else b''
                end=raw.rfind(b'\n')
                if end<0:
                    if size-self.offset>=LIMIT:self.offset=size;self.truncated=True
                    return
                # Revisit only the unfinished trailing line on the next sample.
                self.offset=size-(len(raw)-end-1)
                for line in raw[:end+1].decode('utf-8',errors='replace').splitlines():
                    self.transport_line(line)
                    for name,pattern in APT_EVENTS:
                        if re.match(pattern,line):
                            self.last_event=name
                            self.counts[name]=min(100000,self.counts[name]+1)
                            break
        except OSError:return

    def public(self,now):
        return {'apt_output_last_observed_event':self.last_event,
                'apt_output_observed_event_counts':dict(self.counts),
                'apt_transport_observed_signal_counts':dict(self.transport),
                'apt_source_group_observed_event_counts':dict(self.hosts),
                'apt_output_observation_truncated':self.truncated,
                'apt_output_seconds_since_observed_activity':None if self.last_activity is None else min(3600,max(0,int(now-self.last_activity)))}


def snapshot():
    rows = {}
    for path in pathlib.Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            text = (path / 'stat').read_text()
            fields = text[text.rindex(')') + 2:].split()
            rows[int(path.name)] = (int(fields[1]), fields[19], fields[0])
        except (OSError, ValueError, IndexError):
            pass
    return rows

def descendants(rows, roots):
    result = set(roots)
    changed = True
    while changed:
        new = {pid for pid, row in rows.items() if row[0] in result}
        changed = not new.issubset(result)
        result |= new
    return result

def select_runtime_profile(root, env):
    # Match setup/ubuntu/run-lab.sh without changing runtime fingerprint checks.
    if (pathlib.Path(root)/'src/lab/runtime/native/bridge/profile.json').is_file():
        env['URP_SIM_PROFILE']='image'

def simulation_entry(stage):
    """Explicit stage routing; ordinary acceptance budgets are unchanged."""
    if stage=='warehouse-lifecycle-only':return 'warehouse/warehouse_lifecycle_only_entry.py',3600
    if stage=='warehouse':return 'warehouse/warehouse_acceptance.py',9000
    if stage=='maze':return 'acceptance.py',3600
    raise ValueError('unknown_stage')


def run(args):
    # Adopt only descendants that would otherwise be orphaned during shutdown.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError('subreaper_failed')
    private = pathlib.Path(args.private_dir).resolve()
    private.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(private, 0o700)
    root = pathlib.Path(args.source).resolve()
    harness = pathlib.Path(args.harness).resolve()
    status = {'stage': 'installation', 'exit_code': 1, 'cleanup_remaining': 0, 'forced_kill_count': 0, 'termination_reason': 'unknown'}
    if args.stage == 'warehouse-lifecycle-only':
        status['requested_stage']='warehouse-lifecycle-only'
    owned = {}
    current = None
    env = os.environ.copy()
    # Do not pass action auth or service credentials to package hooks or simulations.
    for name in list(env):
        if any(token in name.upper() for token in ('TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL')) or name.startswith('ACTIONS_'):
            env.pop(name, None)
    (private/'home/.local/bin').mkdir(parents=True)
    env.update(HOME=str(private/'home'), PYTHONDONTWRITEBYTECODE='1', LIBGL_ALWAYS_SOFTWARE='1',
               URP_BIND_HOST='127.0.0.1', URP_RMW='rmw_cyclonedds_cpp',
               UV_PYTHON_INSTALL_DIR=str(private/'python'),
               PATH=str(private/'home/.local/bin') + ':' + env.get('PATH', ''))
    # CLI opt-in is required even if the caller exports the environment variable.
    # Temporary child environment only; preserve the source lock and SRI checks.
    env['npm_config_registry']='https://registry.npmjs.org'
    env['npm_config_replace_registry_host']='registry.npmmirror.com'
    env.pop('URP_HOST_APT_REPAIR',None)
    if getattr(args,'allow_host_apt_repair',False):env['URP_HOST_APT_REPAIR']='1'
    stop = False
    def interrupted(signum, frame):
        nonlocal stop
        stop = True
        status['exit_code'] = 1
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    def track():
        rows = snapshot()
        roots = {os.getpid()} | {pid for pid, stamp in owned.items() if rows.get(pid, (None,None))[1] == stamp}
        for pid in descendants(rows, roots) - {os.getpid()}:
            if pid in rows:
                owned[pid] = rows[pid][1]
        return rows
    def child(command, filename, deadline):
        nonlocal current
        print(json.dumps({'stage':status['stage'],'state':'running'}),flush=True)
        next_progress=time.monotonic()+120
        last_install_phase='unknown'
        last_install_boundary=None
        phase_started=time.monotonic()
        next_phase_sample=0
        apt_observation=None
        with (private / filename).open('wb') as stream:
            current = subprocess.Popen(command, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            while current.poll() is None:
                track()
                now=time.monotonic()
                if status['stage']=='installation' and now >= next_phase_sample:
                    trace=private/'installation-trace.log'
                    found,boundary=installation_trace_state(trace if trace.exists() else private/filename)
                    if found!='unknown' and (found!=last_install_phase or boundary!=last_install_boundary):
                        last_install_phase=found
                        last_install_boundary=boundary
                        phase_started=now
                        apt_observation=AptOutputObservation(private/filename,now) if found in ('apt_update','apt_install') else None
                    if apt_observation is not None:apt_observation.sample(now)
                    next_phase_sample=now+1
                if now >= next_progress:
                    progress={'stage':status['stage'],'state':'running'}
                    if status['stage']=='installation':
                        progress['installation_substage']=last_install_phase
                        progress['installation_substage_observed_seconds']=min(3600,max(0,int(now-phase_started)))
                        if apt_observation is not None:progress.update(apt_observation.public(now))
                    print(json.dumps(progress),flush=True)
                    next_progress=time.monotonic()+120
                if stop:
                    raise StageCancelled()
                if time.monotonic() > deadline:
                    raise StageDeadlineExceeded()
                time.sleep(0.2)
            track()
            status['termination_reason']='completed' if current.returncode==0 else 'child_exit'
            return current.returncode
    try:
        rc = child(['bash', str(pathlib.Path(__file__).with_name('install.sh')), str(root), str(private)], 'install.log', time.monotonic()+3600)
        if rc:
            status['exit_code'] = rc
            return status
        select_runtime_profile(root, env)
        status['stage'] = 'simulation'
        env['PATH'] = str(pathlib.Path(env['HOME'])/'.local/node-v24.22.0/bin') + ':' + env['PATH']
        # Xvfb is owned by this supervisor; Xvfb -displayfd avoids display collisions.
        display = private/'display'
        with display.open('wb') as fd, (private/'xvfb.log').open('wb') as log:
            xvfb = subprocess.Popen(['Xvfb','-displayfd',str(fd.fileno()),'-screen','0','1280x1024x24','-nolisten','tcp'],pass_fds=(fd.fileno(),),stdout=log,stderr=log,env=env,start_new_session=True)
        for _ in range(100):
            track()
            if display.read_text().strip(): break
            if xvfb.poll() is not None: raise RuntimeError('display_failed')
            time.sleep(.1)
        number = display.read_text().strip()
        if not number.isdigit(): raise RuntimeError('display_not_ready')
        env['DISPLAY'] = ':'+number
        entry_name, simulation_budget = simulation_entry(args.stage)
        entry = harness / entry_name
        if args.stage != 'warehouse-lifecycle-only':
            rc = child([str(root/'src/lab/.venv/bin/python'), str(entry), '--source',str(root),'--private-dir',str(private/'harness-private'),'--harness-sha',args.harness_sha,'--summary',str(private/'summary.json'),'--stage',args.stage], 'acceptance.log', time.monotonic()+(9000 if args.stage == 'warehouse' else 3600))
        else:
            diagnostic_deadline=time.monotonic()+3600
            rc = child([str(root/'src/lab/.venv/bin/python'), str(entry), '--source',str(root),'--private-dir',str(private/'harness-private'),'--harness-sha',args.harness_sha,'--summary',str(private/'summary.json'),'--stage',args.stage,'--diagnostic-deadline',str(diagnostic_deadline)], 'acceptance.log', diagnostic_deadline)
        status['exit_code'] = rc
    except StageDeadlineExceeded:
        status['termination_reason']='stage_timeout'
        status['exit_code'] = 1
    except StageCancelled:
        status['termination_reason']='cancelled'
        status['exit_code'] = 1
    except Exception:
        status['termination_reason']='supervisor_error'
        status['exit_code'] = 1
    finally:
        forced_kills = set()
        for sig, seconds in ((signal.SIGTERM,20),(signal.SIGKILL,5)):
            end = time.monotonic()+seconds
            while True:
                rows = track()
                live = {pid for pid,stamp in owned.items() if rows.get(pid,(None,None))[1] == stamp}
                for pid in live:
                    # Zombies cannot receive a meaningful signal; reap them below.
                    if rows[pid][2] == 'Z': continue
                    try:
                        os.kill(pid,sig)
                        if sig == signal.SIGKILL:
                            forced_kills.add((pid,owned[pid]))
                    except (ProcessLookupError, PermissionError): pass
                if not live or time.monotonic() >= end: break
                # Reap direct children, preventing their zombies looking alive.
                try:
                    while os.waitpid(-1,os.WNOHANG)[0]: pass
                except ChildProcessError: pass
                time.sleep(.2)
        rows = snapshot()
        status['cleanup_remaining'] = sum(rows.get(pid,(None,None))[1] == stamp for pid,stamp in owned.items())
        status['forced_kill_count'] = len(forced_kills)
        if status['cleanup_remaining'] or status['forced_kill_count']: status['exit_code'] = 1
        if stop:
            status['exit_code'] = 1
            status['termination_reason']='cancelled'
        logfile = private / ('install.log' if status['stage'] == 'installation' else 'acceptance.log')
        status['failure_hint'] = classify(logfile, status['exit_code'])
        if status['termination_reason']=='child_exit' and status['exit_code']==100 and status['stage']=='installation' and status['failure_hint'] in ('unknown','apt_dependency_conflict'):
            solver_hint=classify(private/'apt-solver.log',status['exit_code'])
            if solver_hint.startswith('apt_'):status['failure_hint']=solver_hint
        trace=private/'installation-trace.log'
        step_log=trace if trace.exists() else logfile
        status['installation_step'] = installation_step(step_log) if status['stage']=='installation' else 'not_applicable'
        if stop:
            status['exit_code'] = 1
            status['failure_hint'] = 'cancelled'
        status['registry_diagnostic'] = {}
        registry=private/'registry-identity.json'
        if registry.exists():
            try:
                if registry.is_symlink() or registry.stat().st_size>4096:raise ValueError('invalid_registry_diagnostic')
                status['registry_diagnostic']=json.loads(registry.read_text())
            except Exception:pass
        registry_status=status['registry_diagnostic']
        if not stop and isinstance(registry_status,dict) and registry_status.get('status')=='failed' and status['stage']=='installation':
            status['installation_step']='registry_check'
            category=registry_status.get('error')
            if category in ('integrity_mismatch','source_not_allowlisted','lock_invalid'):status['failure_hint']='registry_identity'
            elif category in ('http','timeout','tls','network'):status['failure_hint']='network'
        # A supervisor-observed reason outranks all private log heuristics,
        # including a previous registry or resolver failure.
        if status['termination_reason'] in ('stage_timeout','cancelled'):
            status['failure_hint']=status['termination_reason']
        elif status['termination_reason']=='supervisor_error':
            status['failure_hint']='unknown'
        status['apt_projection'] = {'status':'not_run','dependencies':[]}
        status['apt_projection_scope']='not_run'
        projection=private/'apt-dependencies.json'
        if projection.exists():status['apt_projection_scope']='current_attempt'
        elif (private/'apt-dependencies-before-repair.json').exists():
            projection=private/'apt-dependencies-before-repair.json'
            status['apt_projection_scope']='before_repair'
        if projection.exists():
            try:
                if projection.is_symlink() or projection.stat().st_size>32768:raise ValueError('invalid_projection')
                status['apt_projection']=json.loads(projection.read_text())
            except Exception:
                status['apt_projection']={'status':'invalid_projection','dependencies':[]}
        elif (private/'apt-solver.log').exists():
            status['apt_projection']={'status':'metadata_unavailable','dependencies':[]}
        repair_file=private/'host-apt-repair.json'
        status['host_apt_repair']={}
        if repair_file.exists():
            try:
                if repair_file.is_symlink() or repair_file.stat().st_size>4096:raise ValueError('invalid_repair_summary')
                status['host_apt_repair']=json.loads(repair_file.read_text())
            except Exception:pass
        (private/'runner-status.json').write_text(json.dumps(status))
    return status

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--harness',required=True);p.add_argument('--private-dir',required=True);p.add_argument('--stage',choices=['maze','warehouse','warehouse-lifecycle-only'],default='maze');p.add_argument('--harness-sha',required=True)
    p.add_argument('--allow-host-apt-repair',action='store_true')
    try: result=run(p.parse_args());sys.exit(0 if result['exit_code']==0 else 1)
    except Exception: sys.exit(1)
