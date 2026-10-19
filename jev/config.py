"""Role mapping, protected paths and decision-client settings.

routes.example.json always loads first and supplies every key. A local file
is then merged over it: the file named by AGENT_CHECKS_CONFIG (a relative
path is taken from the repository root), else routes.json at the root if it
exists. A local file that is missing or unreadable is reported on stderr and
skipped, so a bad edit cannot crash the hooks.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "routes.example.json"


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    named = os.environ.get("AGENT_CHECKS_CONFIG")
    if named:
        local = Path(named)
        if not local.is_absolute():
            local = ROOT / local
    else:
        local = ROOT / "routes.json"
        if not local.exists():
            return config
    try:
        override = json.loads(local.read_text(encoding="utf-8"))
        if not isinstance(override, dict):
            raise ValueError("top level is not a JSON object")
    except (OSError, ValueError) as exc:
        print(f"agent-checks: ignoring config {local} ({exc}); using the example",
              file=sys.stderr)
        return config
    return _merge(config, override)
