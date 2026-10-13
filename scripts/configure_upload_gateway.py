"""Set upload size only on the IM API location of the existing school gateway."""
from pathlib import Path
import subprocess

path = Path('/etc/nginx/sites-enabled/school-platform.conf')
if path.exists():
    original = path.read_text()
    opening = '    location /im/api/ {'
    start = original.index(opening) + len(opening)
    end = original.index('}', start)
    block = original[start:end]
    import re
    block = re.sub(r'\n\s*client_max_body_size\s+[^;]+;', '', block)
    updated = original[:start] + '\n        client_max_body_size 50M;' + block + original[end:]
    if updated != original:
        backup = path.with_name(path.name + '.before-im-upload-limit')
        if not backup.exists():
            backup.write_text(original)
        path.write_text(updated)
        try:
            subprocess.run(['nginx', '-t'], check=True)
            subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        except Exception:
            path.write_text(original)
            raise
