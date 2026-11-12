"""One serial call with a hard external deadline and independent scoring."""
from collections import Counter
import ctypes
import datetime
import json
import math
import os
import platform
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import threading

_SERIAL_LOCK = threading.Lock()

from .audit import validator
from .io import digest, validate_payload

THREAD_ENV = {k: "1" for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                              "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS")}


def run_payload(payload, *, worker_path=None, python=None):
    """Linux-only serial supervisor; subreaper state is process-global."""
    if sys.platform != "linux":
        raise RuntimeError("CVRPLIB supervision requires Linux")
    if not _SERIAL_LOCK.acquire(blocking=False):
        raise RuntimeError("Concurrent run_payload calls are unsupported; run serially")
    try:
        return _run_payload(payload, worker_path=worker_path, python=python)
    finally:
        _SERIAL_LOCK.release()


def _run_payload(payload, *, worker_path=None, python=None):
    """worker_path is only used by fault-injection tests, never exposed by CLI."""
    validate_payload(payload)
    worker_path = Path(worker_path or Path(__file__).with_name("worker.py")).resolve()
    python = python or sys.executable
    env = {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONHASHSEED": "0", **THREAD_ENV}
    messages, late, malformed = [], [], []
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    timed_out, overflow, done, worker_error = False, False, False, None
    start = time.monotonic_ns()
    deadline = start + int(payload["budget_seconds"]*10**9)
    request = json.dumps({"payload": payload, "deadline_ns": deadline}, allow_nan=False).encode()
    proc, usage, exitcode = None, None, None
    reaped_descendants = []
    exited_before_cleanup = False
    libc = ctypes.CDLL(None, use_errno=True)
    old_subreaper = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(old_subreaper), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
        raise OSError("Linux child-subreaper unavailable")
    with tempfile.TemporaryDirectory(prefix="ceres-cvrplib-") as cwd:
        try:
            proc = subprocess.Popen([python, "-I", str(worker_path)], cwd=cwd,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env, start_new_session=True)
            with selectors.DefaultSelector() as sel:
                os.set_blocking(proc.stdin.fileno(), False)
                sel.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
                pending = memoryview(request)
                for label, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr)):
                    os.set_blocking(pipe.fileno(), False)
                    sel.register(pipe, selectors.EVENT_READ, label)
                while sel.get_map() and not overflow:
                    remaining = (deadline-time.monotonic_ns())/10**9
                    if remaining <= 0:
                        timed_out = True
                        break
                    for key, _ in sel.select(min(remaining, .05)):
                        if key.data == "stdin":
                            try:
                                written = os.write(key.fileobj.fileno(), pending[:8192])
                                pending = pending[written:]
                            except BrokenPipeError:
                                pending = pending[len(pending):]
                            if not pending:
                                sel.unregister(key.fileobj)
                                key.fileobj.close()
                            continue
                        data = os.read(key.fileobj.fileno(), 65536)
                        received = time.monotonic_ns()
                        label = key.data
                        if not data:
                            sel.unregister(key.fileobj)
                            continue
                        chunks[label].extend(data)
                        if sum(map(len, chunks.values())) > 4*1024**2:
                            overflow = True
                            break
                        buffers[label].extend(data)
                        if label != "stdout":
                            continue
                        while b"\n" in buffers[label]:
                            raw, _, rest = buffers[label].partition(b"\n")
                            buffers[label] = bytearray(rest)
                            try:
                                def bad_constant(value):
                                    raise ValueError("nonfinite JSON constant " + value)
                                row = json.loads(raw, parse_constant=bad_constant)
                                if not isinstance(row, dict) or type(row.get("emitted_ns")) is not int:
                                    raise ValueError("missing event timestamp")
                                if row["event"] not in {"incumbent", "phase", "done", "error", "deadline"}:
                                    raise ValueError("unknown worker event")
                                if row["event"] == "error" and (not isinstance(row.get("error_type"), str) or not isinstance(row.get("message"), str)):
                                    raise ValueError("malformed error event")
                                row["received_ns"] = received
                                if not start <= row["emitted_ns"] <= received <= deadline:
                                    late.append(row)
                                    continue
                                messages.append(row)
                                done |= row["event"] == "done"
                                timed_out |= row["event"] == "deadline"
                                if row["event"] == "error":
                                    worker_error = row
                            except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
                                malformed.append(str(exc))
                # EOF is not proof of process exit. Wait only within budget so
                # normal exit is observed rather than racing our cleanup kill.
                while not sel.get_map() and not overflow:
                    if os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
                        break
                    remaining = (deadline-time.monotonic_ns())/10**9
                    if remaining <= 0:
                        timed_out = True
                        break
                    time.sleep(min(.001, remaining))
                # Stop at the deadline even if a native call ignores the Python
                # callback. Data read after this point can never become a result.
        finally:
            cleanup_start = time.monotonic_ns()
            if proc is not None:
                exited_before_cleanup = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _, status, usage = os.wait4(proc.pid, 0)
                exitcode = os.waitstatus_to_exitcode(status)
                proc.returncode = exitcode
                # The worker is a session/process-group leader. Adopt and reap
                # its killed descendants as well, without touching other groups.
                reap_deadline = time.monotonic()+.25
                while time.monotonic() < reap_deadline:
                    try:
                        child, child_status, child_usage = os.wait4(-proc.pid, os.WNOHANG)
                    except ChildProcessError:
                        break
                    if not child:
                        time.sleep(.005)
                        continue
                    reaped_descendants.append({"pid": child, "exitcode": os.waitstatus_to_exitcode(child_status),
                                               "cpu_seconds": child_usage.ru_utime+child_usage.ru_stime})
                for label, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr)):
                    try:
                        tail = pipe.read() or b""
                        chunks[label].extend(tail)
                    finally:
                        pipe.close()
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            cleanup_seconds = (time.monotonic_ns()-cleanup_start)/10**9
            libc.prctl(36, old_subreaper.value, 0, 0, 0)
    end = time.monotonic_ns()
    check_start, check_cpu = time.monotonic_ns(), time.process_time()
    checked = []
    for row in messages:
        if row["event"] == "incumbent":
            if type(row.get("cost")) is not int or row.get("cost", -1) < 0:
                checked.append({"valid": False, "errors": ["non-integer/nonfinite/missing cost"]})
            else:
                checked.append(validator.validate_solution(payload, row.get("routes"), row["cost"]))
    incumbents = [row for row in messages if row["event"] == "incumbent"]
    valid_pairs = [(row, c) for row,c in zip(incumbents, checked) if c["valid"]]
    best = min(valid_pairs, key=lambda pair: pair[1]["cost"]) if valid_pairs else None
    if any(not c["valid"] for c in checked):
        status = "NUMERIC_ERROR" if any("COST" in e or "cost" in e for c in checked for e in c["errors"]) else "INVALID_SOLUTION"
    elif malformed or buffers["stdout"]:
        status = "API_ERROR"
    elif overflow:
        status = "RESOURCE_LIMIT"
    elif worker_error:
        status = "CONSTRUCTION_FAILURE" if worker_error["error_type"] in {"InitFailure", "ConstructionFailure"} else ("RESOURCE_LIMIT" if worker_error["error_type"] in {"MemoryError"} else "API_ERROR")
    elif exited_before_cleanup and exitcode != 0:
        status = "RESOURCE_LIMIT" if exitcode in (-signal.SIGXCPU, -signal.SIGKILL) else "CRASH"
    elif timed_out or late:
        status = "TIMEOUT_WITH_FEASIBLE_INCUMBENT" if best else "TIMEOUT_NO_SOLUTION"
    elif exitcode != 0 or not done:
        status = "RESOURCE_LIMIT" if exitcode in (-signal.SIGXCPU, -signal.SIGKILL) else "CRASH"
    else:
        status = "OK" if best else "NO_SOLUTION"
    feasible = status in {"OK", "TIMEOUT_WITH_FEASIBLE_INCUMBENT"} and best is not None
    return {"schema": "ceres-cvrplib-result-v1", "purpose": "acceptance_only", "status": status,
            "algorithm": payload["algorithm"], "seed": payload["seed"],
            "budget_seconds": payload["budget_seconds"], "payload_sha256": digest(payload),
            "start_monotonic_ns": start, "deadline_monotonic_ns": deadline,
            "cpu_seconds": usage.ru_utime+usage.ru_stime if usage else None,
            "cpu_scope": "worker process including interpreter/import/model/initialization/search; wait4 RUSAGE, not solver loop",
            "descendant_cpu_seconds": sum(c["cpu_seconds"] for c in reaped_descendants),
            "budget_scope": "wall from before request serialization/process launch through checkpoint receipt; checker and cleanup excluded",
            "maxrss_KiB": usage.ru_maxrss if usage else None,
            "process_wall_seconds": (end-start)/10**9, "cleanup_wall_seconds": cleanup_seconds,
            "checker_wall_seconds": (time.monotonic_ns()-check_start)/10**9,
            "checker_cpu_seconds": time.process_time()-check_cpu,
            "exitcode": exitcode, "reaped": proc is not None,
            "exited_before_cleanup": exited_before_cleanup,
            "supervision_scope": "Linux serial; process-group descendants reaped; reviewed worker prohibits Python process creation; not a hostile native-code sandbox",
            "reaped_descendants": reaped_descendants,
            "feasible": feasible, "cost": best[1]["cost"] if feasible else None,
            "routes": best[0]["routes"] if feasible else None,
            "incumbent_emitted_ns": best[0]["emitted_ns"] if feasible else None,
            "incumbent_received_ns": best[0]["received_ns"] if feasible else None,
            "events": messages, "late_events_rejected": late, "malformed_events": malformed,
            "checkpoint_checks": checked, "worker_error": worker_error,
            "raw_stdout": chunks["stdout"].decode("utf-8", errors="replace"),
            "raw_stderr": chunks["stderr"].decode("utf-8", errors="replace"),
            "environment": {"python": str(python), "python_version": sys.version, "platform": platform.platform(), "threads": THREAD_ENV},
            "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}


def summarize(rows, planned):
    """Never turn survivor means into all-plan quality, or missing into zero."""
    if not isinstance(rows,(list,tuple)) or type(planned) is not int or planned < len(rows):
        raise ValueError("planned must cover every attempt")
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('status'),str) or not row['status'] or type(row.get('feasible')) is not bool:
            raise ValueError('result rows require a status and boolean feasibility')
        if row['feasible']:
            if row['status'] not in {'OK','TIMEOUT_WITH_FEASIBLE_INCUMBENT'} or type(row.get('cost')) is not int or row['cost']<0:
                raise ValueError('feasible result has contradictory status or invalid cost')
        elif row.get('cost') is not None:
            raise ValueError('failed result cost must be null')
        key=row.get('run_key')
        if key is not None:
            if not isinstance(key,str) or not key or key in seen:
                raise ValueError('invalid or duplicate run key in summary')
            seen.add(key)
    good = [r for r in rows if r.get("feasible") and r.get("cost") is not None]
    return {"planned": planned, "attempted": len(rows), "not_started": planned-len(rows),
            "feasible": len(good), "feasible_rate_planned": len(good)/planned if planned else None,
            "status_counts": dict(Counter(r["status"] for r in rows)),
            "all_planned_mean_cost": sum(r["cost"] for r in good)/planned if planned and len(good)==planned else None,
            "available_mean_cost": sum(r["cost"] for r in good)/len(good) if good else None,
            "available_denominator": len(good)}
