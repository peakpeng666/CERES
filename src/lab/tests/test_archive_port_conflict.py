"""A gateway must never proxy archive requests back into itself."""
import pytest

from urp_lab.gateway import Config, create_app


@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost', '[::1]'])
def test_local_archive_port_conflict_rejected_before_startup(tmp_path, host):
    with pytest.raises(RuntimeError, match='档案后端地址冲突'):
        create_app(Config(port=4180, archive_url=f'http://{host}:4180', runtime=tmp_path, background=False))
    assert not (tmp_path / 'lab.sqlite3').exists()
