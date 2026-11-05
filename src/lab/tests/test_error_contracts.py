"""Input and dependency failures must not become fabricated defaults or success."""
import sys
from types import SimpleNamespace

import httpx
import pytest

from urp_lab.agent.llm import ModelError, OpenAICompatibleModel
from urp_lab.compose import compose
from urp_lab.jobs import resolve_params
from urp_lab.recommend import recommend
from urp_lab.solvers import GRID_PARAMS, OMPL_PARAMS, PARAMS


@pytest.mark.parametrize('params', [PARAMS, GRID_PARAMS, OMPL_PARAMS])
def test_explicit_null_is_not_an_algorithm_default(params):
    algorithm = {'id': 'test', 'params': params}
    for param in params:
        with pytest.raises(ValueError, match=param['name']):
            resolve_params(algorithm, {param['name']: None})
    assert resolve_params(algorithm, None) == {p['name']: p['default'] for p in params}


def test_parsed_zero_seed_is_preserved():
    parsed = compose('采样 seed 0')
    explicit = compose('采样 seed 0', seed=0)
    assert parsed['provenance']['seed'] == 0
    assert parsed['instance'] == explicit['instance']


def test_recommendation_does_not_invent_convexity_on_geometry_failure(monkeypatch):
    import shapely.geometry

    def broken_polygon(*args):
        raise RuntimeError('geometry failure')

    monkeypatch.setattr(shapely.geometry, 'Polygon', broken_polygon)
    with pytest.raises(RuntimeError, match='geometry failure'):
        recommend({'problem_class': 'coverage', 'frame': {'kind': 'map'}},
                  {'swath_width': 1, 'turn_radius': 1, 'field': [[0, 0], [1, 0], [0, 1]]})


@pytest.mark.parametrize('planner', ['motion', 'fleet'])
def test_seed_failure_aborts_before_planning(monkeypatch, planner):
    from urp_lab.solvers.motion import plan_ompl
    from urp_lab.solvers.fleet_curvature import ompl_dubins_fallback

    def failed_seed(seed):
        raise RuntimeError('cannot set seed')

    util = SimpleNamespace(setLogLevel=lambda level: None, LogLevel=SimpleNamespace(LOG_NONE=0),
                           RNG=SimpleNamespace(setSeed=failed_seed))
    monkeypatch.setitem(sys.modules, 'ompl', SimpleNamespace(base=object(), geometric=object(), util=util))
    with pytest.raises(RuntimeError, match='cannot set seed'):
        if planner == 'motion':
            plan_ompl('unused', {}, 'ompl.rrt_star', {'seed': 7})
        else:
            ompl_dubins_fallback({}, [], 1)


def model_reply(message):
    model = OpenAICompatibleModel('https://example.test/v1', 'test', 'secret',
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'choices': [{'message': message}]})))
    return model.complete([], [])


@pytest.mark.parametrize('message', [None, [], 'text', {'content': {}}, {'content': 42},
    {'tool_calls': {}}, {'tool_calls': 'calls'}, {'tool_calls': [None]},
    {'tool_calls': [{'function': []}]}, {'tool_calls': [{'function': {'name': ''}}]},
    *[{'tool_calls': [{'function': {'name': 'list_algorithms', 'arguments': args}}]}
      for args in (None, False, [], {}, 0)],
    {'tool_calls': [{'function': {'name': 'list_algorithms'}}]}])
def test_malformed_model_messages_raise_model_error(message):
    with pytest.raises(ModelError):
        model_reply(message)


def test_model_keeps_invalid_json_arguments_for_explicit_tool_error():
    reply = model_reply({'content': None, 'tool_calls': [{'id': 'x',
        'function': {'name': 'list_algorithms', 'arguments': '{broken'}}]})
    assert reply.content == '' and reply.tool_calls[0]['arguments'] == '{broken'


@pytest.mark.parametrize('payload', [{}, {'metrics': {'cost': 0}}, {'status': 'success'}, {'status': None}, {'status': []}])
def test_worker_json_requires_a_terminal_solver_status(payload):
    import json
    import threading
    from urp_lab.jobs import JobManager
    manager = JobManager.__new__(JobManager)
    manager._lock, manager._canceled = threading.Lock(), set()
    sol = {'run_id': 'test', 'metrics': {}, 'routes': [], 'algorithm': {}}
    manager._finish(sol, 0, json.dumps(payload).encode(), b'', .1, 1, False)
    assert sol['state'] == 'error'
    assert 'status' in sol['error']


@pytest.mark.parametrize('error', [PermissionError(1, 'permission denied'), RuntimeError('lock API bug'), KeyboardInterrupt()])
def test_store_does_not_misreport_all_lock_errors_as_existing_owner(tmp_path, monkeypatch, error):
    import fcntl
    from urp_lab.store import Store

    def fail(*args):
        raise error

    monkeypatch.setattr(fcntl, 'flock', fail)
    with pytest.raises(type(error)) as caught:
        Store(tmp_path / 'lab.sqlite')
    assert caught.value is error
