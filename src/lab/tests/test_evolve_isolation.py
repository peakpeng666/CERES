"""No host dataset reads and no token-prefix bypass in the offline integration."""
import pytest

from urp_lab.evolve import instances
from urp_lab.library import fields


def test_field_cache_is_fresh_empty_and_does_not_project_geometry(tmp_path, monkeypatch):
    assert fields.DIR == tmp_path / 'isolated-fields2benchmark'
    assert fields.INDEX == fields.DIR / 'index.json'

    def no_projection(*args, **kwargs):
        raise AssertionError('No real geometry may be projected in offline contracts')

    monkeypatch.setattr(fields, '_project', no_projection)
    assert list(fields.all_ids()) == []
    assert fields.status()['downloaded'] is False
    assert list(fields.DIR.iterdir()) == []


def test_field_download_is_blocked():
    with pytest.raises(AssertionError, match='must not download'):
        fields.fetch()


@pytest.mark.parametrize('token', [None, 'FROZEN:', 'FROZEN:forged', 'FROZEN:' + '0' * 64])
def test_sealed_builders_reject_tokens_before_accessing_geometry(token, monkeypatch):
    def no_geometry(*args, **kwargs):
        raise AssertionError('Sealed builder reached geometry')

    monkeypatch.setattr(fields, 'scenario', no_geometry)
    with pytest.raises(PermissionError, match='disabled'):
        instances.coverage_instance('lt_field_1', 'tractor_m', token=token)
    with pytest.raises(PermissionError, match='disabled'):
        instances.fleet_instance('ee_field_5', 10, 'uniform', 'euclidean',
                                 'total_distance', 1, False, token=token)
