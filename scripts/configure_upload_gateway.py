"""Set upload size only on the IM API location of the existing school gateway."""
from pathlib import Path
import re
import subprocess


def configure_gateway(path=Path('/etc/nginx/sites-enabled/school-platform.conf')):
    if not path.exists():
        return
    # sites-enabled/* includes every file, including files with backup suffixes.
    backup_dir = path.parent.parent / 'im-config-backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (path.name + '.before-im-upload-limit')
    legacy = path.with_name(path.name + '.before-im-upload-limit')
    if legacy.exists():
        legacy.replace(backup if not backup.exists() else backup.with_name(backup.name + '.legacy'))
    original = path.read_text()
    opening = '    location /im/api/ {'
    start = original.index(opening) + len(opening)
    end = original.index('}', start)
    block = re.sub(r'\n\s*client_max_body_size\s+[^;]+;', '', original[start:end])
    updated = original[:start] + '\n        client_max_body_size 50M;' + block + original[end:]
    if updated != original:
        if not backup.exists():
            backup.write_text(original)
        path.write_text(updated)
        try:
            subprocess.run(['nginx', '-t'], check=True)
            subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        except Exception:
            path.write_text(original)
            raise


if __name__ == '__main__':
    configure_gateway()
