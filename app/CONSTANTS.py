data_source_redirect="redirect_table"
data_source_upstream="upstream_table"
data_source_redis="redis cache"
upstreamCheckLogTable="upstream_check_log_table"
KEY_DATA_TYPE="type"
DATA_TYPE_DYNAMIC="dynamic"
DATA_TYPE_STATIC="static"
DATA_TYPE_USER_DYNAMIC="user-dynamic"
import subprocess
import re
import os

def _base_version():
    # Read VERSION file at project root
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        vfile = os.path.join(root, "VERSION")
        if os.path.isfile(vfile):
            with open(vfile, encoding="utf-8") as f:
                v = f.read().strip()
                if v:
                    return v
    except Exception:
        pass
    return "3.1.0"

def get_semver():
    try:
        base = _base_version()
        # Try git describe for precise build suffix
        try:
            desc = subprocess.check_output(['git', 'describe', '--tags', '--long', '--match', 'v*'], encoding='utf-8').strip()
            m = re.match(r'v?(\d+\.\d+\.\d+)-(\d+)-g([0-9a-f]+)', desc)
            if m:
                _, commits, githash = m.groups()
                if int(commits) == 0:
                    return base
                return f"{base}+{commits}.g{githash}"
        except Exception:
            pass
        # Fallback to commit count
        commit_count = int(subprocess.check_output(['git', 'rev-list', '--count', 'HEAD'], encoding='utf-8').strip())
        githash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], encoding='utf-8').strip()
        return f"{base}+{commit_count}.g{githash}"
    except Exception:
        return _base_version()

__version__ = get_semver()