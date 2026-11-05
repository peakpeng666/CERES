"""Diagnostic-only decomposition of the reviewed bootstrap prerequisite.

No values, exception text or paths are emitted. The caller maps only these fixed
exit codes to its closed diagnostic vocabulary. This checks the bootstrap's
system Python, not the later CPython 3.12.13 native-build target.
"""


def check(importer=__import__):
    # Preserve the old command's import order and import-before-predicate order.
    try:
        system = importer('sys')
    except Exception:
        return 61
    try:
        apt_pkg = importer('apt_pkg')
    except Exception:
        return 62
    try:
        ssl = importer('ssl')
    except Exception:
        return 63

    # Keep all three original predicates, including their short-circuit order.
    # apt_pkg.VERSION is APT's version, not the python-apt package's version.
    try:
        if not (system.version_info[:2] == (3, 10)):
            return 65
    except Exception:
        return 64
    try:
        if not apt_pkg.VERSION.startswith('2.4.'):
            return 67
    except Exception:
        return 66
    try:
        if not ssl.get_default_verify_paths().cafile:
            return 69
    except Exception:
        return 68
    return 0


if __name__ == '__main__':
    raise SystemExit(check())
