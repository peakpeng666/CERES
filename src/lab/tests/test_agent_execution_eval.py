"""Execution evidence must not report a successful process for failing cases."""
import sys

import pytest

from urp_lab.agent.evals import execution


@pytest.mark.parametrize(('passed', 'scenarios', 'expected'), [(5, 5, 0), (4, 5, 1), (0, 0, 1)])
def test_execution_cli_exit_code(monkeypatch, tmp_path, passed, scenarios, expected):
    monkeypatch.setattr(sys, 'argv', ['execution', '--mode', 'rule', '--out', str(tmp_path / 'new')])
    monkeypatch.setattr(execution, 'run', lambda *args: {
        'meta': {'passed': passed, 'scenarios': scenarios}, 'records': [],
    })
    assert execution.main() == expected
