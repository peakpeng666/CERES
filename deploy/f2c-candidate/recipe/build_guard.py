"""Check Linux kernel namespace state, not a caller-supplied environment flag."""
import fcntl
import os
import pathlib
import socket
import struct

from guardlib import require

def network_guard(baseline, proc=pathlib.Path('/proc'), readlink=os.readlink, ioctl=fcntl.ioctl):
    boot = (proc / 'sys/kernel/random/boot_id').read_text().strip()
    require(boot == baseline['kernel_boot_id'], 'Kernel boot changed; re-prepare and review baseline')
    mine = readlink(proc / 'self/ns/net')
    require(mine != baseline['netns'], 'Not isolated from reviewed original host namespace')
    try:
        init = readlink(proc / '1/ns/net')
    except PermissionError:
        init = None
    require(mine != init, 'Not isolated from PID1 network namespace')
    interfaces = [x.split(':', 1)[0].strip() for x in (proc / 'net/dev').read_text().splitlines()[2:] if x.strip()]
    require(interfaces == ['lo'], 'Unexpected network interfaces')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        flags = struct.unpack('16sH14x', ioctl(sock, 0x8913, struct.pack('16sH14x', b'lo', 0)))[1]
    require(not flags & 1, 'Loopback is UP; require pristine unshare namespace')
    require(not any(x.strip() for x in (proc / 'net/route').read_text().splitlines()[1:]), 'Unexpected IPv4 route')
    for row in (proc / 'net/ipv6_route').read_text().splitlines():
        require(not row.strip() or row.split()[-1] == 'lo', 'Unexpected IPv6 route')
    for fd in (proc / 'self/fd').iterdir():
        try:
            target = readlink(fd)
        except FileNotFoundError:
            continue
        require(not target.startswith('socket:'), 'Inherited socket descriptor')
    return {'network_isolated': True, 'interfaces': interfaces, 'loopback_flags': flags}
