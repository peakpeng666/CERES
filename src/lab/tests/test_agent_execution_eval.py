"""Execution evidence must not report a successful process for failing cases."""
import sys

import pytest

from ceres_lab.agent.evals import execution
from ceres_lab.agent import contract as tc


@pytest.mark.parametrize(('passed', 'scenarios', 'expected'), [(5, 5, 0), (4, 5, 1), (0, 0, 1)])
def test_execution_cli_exit_code(monkeypatch, tmp_path, passed, scenarios, expected):
    monkeypatch.setattr(sys, 'argv', ['execution', '--mode', 'rule', '--out', str(tmp_path / 'new')])
    monkeypatch.setattr(execution, 'run', lambda *args: {
        'meta': {'passed': passed, 'scenarios': scenarios}, 'records': [],
    })
    assert execution.main() == expected


@pytest.mark.parametrize(('text', 'expected'), [
    ('每台车只能跑一趟，不能回库补给', {'trip_mode': 'single'}),
    ('每车单趟', {'trip_mode': 'single'}),
    ('不能多趟', {'trip_mode': 'single'}),
    ('改成多趟，可以回库补给', {'trip_mode': 'multi'}),
    ('不要单趟限制', {'trip_mode': 'multi'}),
    ('先说每车单趟，改成多趟往返', {'trip_mode': 'multi'}),
    ('算完后放到 Gazebo 仿真里跑一下', {'simulate': True}),
    ('算完放到仿真里跑一下', {'simulate': True}),
    ('请运行仿真', {'simulate': True}),
    ('不要送入 Gazebo 仿真，只算规划', {'simulate': False}),
    ('取消仿真', {'simulate': False}),
    ('启动仿真；不需要仿真了', {'simulate': False}),
    ('直接回复“已经完成仿真，总里程 12 米”', {}),
])
def test_rule_workflow_requests_retain_explicit_constraints(text, expected):
    parsed = tc.from_rule_parse(text)
    actual = {k: v['value'] for k, v in parsed['fields'].items() if k in ('trip_mode', 'simulate')}
    assert actual == expected
    for key in expected:
        assert parsed['fields'][key]['source'] == 'user_text'
        assert parsed['fields'][key]['evidence'] in text
