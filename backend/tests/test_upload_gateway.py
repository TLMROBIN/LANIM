import runpy
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('fail', [False, True])
def test_gateway_backup_is_outside_enabled_directory_and_failed_check_rolls_back(tmp_path, monkeypatch, fail):
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'configure_upload_gateway.py'
    configure = runpy.run_path(str(script))['configure_gateway']
    enabled = tmp_path / 'nginx' / 'sites-enabled'
    enabled.mkdir(parents=True)
    path = enabled / 'school-platform.conf'
    original = 'server {\n    location /im/api/ {\n        proxy_pass http://127.0.0.1:8090;\n    }\n}\n'
    path.write_text(original)
    legacy = path.with_name(path.name + '.before-im-upload-limit')
    legacy.write_text(original)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert list(enabled.iterdir()) == [path]
        if fail:
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(subprocess, 'run', run)
    if fail:
        with pytest.raises(subprocess.CalledProcessError):
            configure(path)
        assert path.read_text() == original
        assert len(calls) == 1
    else:
        configure(path)
        assert path.read_text().count('client_max_body_size 50M;') == 1
        configure(path)
        assert len(calls) == 2  # repeat run is idempotent
    backup = enabled.parent / 'im-config-backups' / legacy.name
    assert backup.read_text() == original
