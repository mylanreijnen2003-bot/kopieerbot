"""In GitHub Actions: volledige wallet-adressen in logs afkorten (logs van een openbare repo zijn openbaar)."""
import os as _os
import re as _re
import sys as _sys

_ADR = _re.compile(r"0x[0-9a-fA-F]{40}")


class _Masker:
    def __init__(self, f):
        self._f = f

    def write(self, t):
        if isinstance(t, str):
            t = _ADR.sub(lambda m: m.group()[:6] + "…" + m.group()[-4:], t)
        return self._f.write(t)

    def __getattr__(self, n):
        return getattr(self._f, n)


if _os.environ.get("GITHUB_ACTIONS") == "true" and not isinstance(_sys.stdout, _Masker):
    _sys.stdout = _Masker(_sys.stdout)
    _sys.stderr = _Masker(_sys.stderr)
