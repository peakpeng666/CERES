#!/usr/bin/env python3
"""Private container networking; forward only the existing three local endpoints."""
import asyncio
import json
import os
import signal
import subprocess
import sys


async def pipe(reader, writer):
    try:
        while data := await reader.read(256 * 1024):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def forward(reader, writer, port):
    try:
        remote_reader, remote_writer = await asyncio.open_connection('127.0.0.1', port)
        await asyncio.gather(pipe(reader, remote_writer), pipe(remote_reader, writer))
    except (OSError, ConnectionError):
        writer.close()


async def serve(address, command):
    servers = []
    # gzweb already binds 0.0.0.0:9002. Proxying that port would prevent its
    # listener from starting; Docker publishes it directly to host loopback.
    for port in (4100, 8765):
        servers.append(await asyncio.start_server(lambda r, w, p=port: forward(r, w, p), address, port))
    child = await asyncio.create_subprocess_exec(*command)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, lambda s=sig: child.send_signal(s) if child.returncode is None else None)
    code = await child.wait()
    for server in servers:
        server.close()
        await server.wait_closed()
    return code if code >= 0 else 128 - code


def main():
    # NET_ADMIN applies only inside Docker's network namespace. Host routes and
    # security settings are never changed. Drop to the application UID afterward.
    if os.geteuid() != 0:
        raise SystemExit('Container entry requires its default root initialization user')
    host_netns = os.environ.get('CERES_HOST_NETNS')
    if not host_netns or host_netns == os.readlink('/proc/self/ns/net'):
        raise SystemExit('Use setup/run.sh reproduce with a private network namespace; refusing host changes')
    subprocess.run(['ip', 'link', 'set', 'lo', 'multicast', 'on'], check=True)
    subprocess.run(['ip', 'route', 'replace', '239.255.0.0/16', 'dev', 'lo'], check=True)
    links = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr', 'show', 'scope', 'global']))
    address = next((a['local'] for link in links for a in link['addr_info'] if a['family'] == 'inet'), None)
    if not address:
        raise SystemExit('Use a private Docker bridge network; host/none networking is unsupported')
    authority = os.environ.get('CERES_XAUTHORITY_INPUT')
    if authority:
        from pathlib import Path
        target = Path('/tmp/ceres-xauthority')
        target.write_bytes(Path(authority).read_bytes())
        target.chmod(0o600)
        os.chown(target, 1000, 1000)
        os.environ['XAUTHORITY'] = str(target)
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
    # no_new_privs prevents helpers from regaining privilege through setuid files.
    import ctypes
    if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), 'PR_SET_NO_NEW_PRIVS failed')
    return asyncio.run(serve(address, sys.argv[1:] or ['bash', 'setup/run.sh', 'lab']))


if __name__ == '__main__':
    sys.exit(main())
