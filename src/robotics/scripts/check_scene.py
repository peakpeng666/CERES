#!/usr/bin/env python3
"""Offline contract/semantic check, no ROS, URL fetches, or commands."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ceres_route_sim'))
from ceres_route_sim.scene_core import prepare_scene,read_json
p=argparse.ArgumentParser()
p.add_argument('scene_file')
p.add_argument('--calibration-file')
p.add_argument('--require-metric', action='store_true')
a=p.parse_args()
try:
    view=prepare_scene(read_json(a.scene_file),read_json(a.calibration_file) if a.calibration_file else None,a.require_metric)
    print(json.dumps({'valid':True,'frame':view.frame,'metric_display':view.metric,
                      'point_count':len(view.points),'vehicle_count':len(view.routes),
                      'simulation_ready':False,'label':view.label,
                      'identity':view.identity_label,'status':view.status_label,
                      'experiment_fingerprint':view.experiment_fingerprint},indent=2))
except (ValueError,OSError) as exc:
    print(json.dumps({'valid':False,'error':str(exc)}),file=sys.stderr)
    sys.exit(1)
