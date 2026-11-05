#!/usr/bin/env python3
"""Exercise agriculture through the deployed HTTP API, without ROS assets or third-party Python packages."""
import argparse
import json
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def request_json(base, path, body=None, expected=200):
    headers = {"Origin": base}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = Request(base + "/lab/api" + path, data=data, headers=headers)
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=10)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read().decode()
        require(response.code == expected, f"{path}: expected HTTP {expected}, got {response.code}")
        return json.loads(raw)


def run(base, timeout=60, *, obstacle=False):
    base = base.rstrip("/")
    parsed = urlparse(base)
    require(parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and not parsed.username and not parsed.password and parsed.path in {"", "/"}
            and not parsed.query and not parsed.fragment, "Use a local HTTP gateway base URL")
    require(0 < timeout <= 120, "Timeout must be in (0, 120] seconds")
    listing = request_json(base, "/scenarios")
    require(isinstance(listing, dict) and isinstance(listing.get("scenarios"), list), "Scenario listing is invalid")
    scene_id = "agri-ci-" + uuid.uuid4().hex
    params = {"field_w": 200, "field_h": 140, "swath_width": 3, "headland_width": 6,
              "turn_radius": 4.5, "robot_width": 2.2, "route_pattern": "boustrophedon",
              "pattern_id": "coverage-tillage", "builder_version": "urp.agri.builder/3"}
    scene = {"id": scene_id, "title": "Agriculture deployment smoke", "problem_class": "coverage",
             "frame": {"kind": "field", "units": "m", "crs": "local"}, "cost_unit": "m",
             "provenance": {"generator": "urp.studio.agri", "params": params, "seed": 42},
             "instance": {"field": [[0, 0], [200, 0], [200, 140], [0, 140]], "obstacles": [],
                          "headland_width": 6, "swath_width": 3, "turn_radius": 4.5,
                          "robot_width": 2.2, "start": [3, 3], "route_pattern": "boustrophedon", "angle_deg": None}}
    if obstacle:
        # An interior hole forces safe transit connections between split swaths.
        scene["instance"]["obstacles"] = [[[80, 50], [120, 50], [120, 90], [80, 90]]]
        params["obstacle_fixture"] = "central-hole-v1"
    request_json(base, "/scenarios", scene, 201)
    restored = request_json(base, "/scenarios/" + scene_id)
    require(restored.get("provenance", {}).get("params") == params and restored["provenance"].get("seed") == 42,
            "Saved scene lost reproducibility metadata")
    request_json(base, "/scenarios", {**scene, "title": "must not overwrite"}, 409)
    require(request_json(base, "/scenarios/" + scene_id) == restored, "Duplicate import changed original scene")
    job = request_json(base, "/solve", {"scenario_id": scene_id, "algorithms": [{"id": "panel.boustrophedon", "params": {}}],
                                      "label": "agriculture deployment smoke"}, 202)
    require(bool(job.get("runs")), "Solve returned no run IDs")
    run_id = job["runs"][0]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = request_json(base, "/runs/" + run_id)
        if result.get("state") in {"done", "error", "cancelled", "canceled", "timeout"}:
            break
        time.sleep(0.25)
    else:
        raise RuntimeError(f"Solve did not complete within {timeout}s: {run_id}")
    require(result.get("state") == "done" and result.get("status") == "feasible", "Solve failed")
    require(result.get("check", {}).get("valid") is True, "Independent check failed")
    record = request_json(base, "/runs/" + run_id + "/record")
    provenance = record.get("record", {}).get("scenario", {}).get("provenance", {})
    require(provenance.get("params") == params and provenance.get("seed") == 42, "Record lost reproducibility metadata")
    return {"scene_id": scene_id, "run_id": run_id, "status": "passed", "duplicate_status": 409,
            "independent_check": True, "metadata_roundtrip": True, "obstacle": obstacle}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:4100")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--obstacle", action="store_true", help="Exercise coverage transit around a fixed interior hole")
    args = parser.parse_args()
    print(json.dumps(run(args.base_url, args.timeout, obstacle=args.obstacle), indent=2))
