#!/usr/bin/env python3
"""Combine controller exit status, report checks and existence of telemetry."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ceres_route_sim'))
from ceres_route_sim.runtime_core import report_failures
from ceres_route_sim.scene_core import read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--exit-code', type=int, required=True)
    args = parser.parse_args()
    path = Path(args.report)
    try:
        report = read_json(path)
        failures = report_failures(report, args.exit_code)
    except (OSError, ValueError) as exc:
        failures = ['missing_or_invalid_report:' + str(exc)]
    telemetry = path.parent / 'telemetry.jsonl'
    telemetry_hash = None
    try:
        digest = hashlib.sha256()
        size = 0
        with telemetry.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
                size += len(chunk)
        if size == 0:
            failures.append('empty_telemetry')
        else:
            telemetry_hash = digest.hexdigest()
    except OSError:
        failures.append('missing_telemetry')
    result = {'stage': 'runner_gate', 'success': not failures,
              'controller_exit_code': args.exit_code, 'failed_checks': failures,
              'telemetry_sha256': telemetry_hash,
              'scope': 'SYNTHETIC_GREENHOUSE_ONLY',
              'limitation': 'Report consistency gate; not an independent physical or collision checker.'}
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
