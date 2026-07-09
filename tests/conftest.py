"""LHM test bootstrap — import-path belt-and-braces (narrowed, QGR L0 F3).

The editable install is the real mechanism. macOS/uv occasionally writes the
``__editable__.lhm-*.pth`` with the APFS ``UF_HIDDEN`` flag, which ``site.py``
silently SKIPS (instinct: uv-editable-pth-uf-hidden-breaks-import). The
fallback below fires ONLY when that exact condition is detected — a hidden
lhm .pth present in site-packages — so a genuinely broken install still reds
the suite instead of being papered over by a source-tree import.
"""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path

_UF_HIDDEN = 0x8000  # macOS chflags hidden


def _hidden_lhm_pth_present() -> bool:
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for pth in Path(sp).glob("__editable__.lhm-*.pth"):
            try:
                if os.stat(pth).st_flags & _UF_HIDDEN:  # type: ignore[attr-defined]
                    return True
            except (AttributeError, OSError):  # non-mac / race
                continue
    return False


try:  # the editable install (normal path)
    import lhm  # noqa: F401
except ModuleNotFoundError:
    if _hidden_lhm_pth_present():
        # The known APFS flag quirk — rescue loudly.
        print(
            "WARNING: lhm editable .pth is UF_HIDDEN (site.py skipped it) — "
            "falling back to the source tree. Run `chflags nohidden` on the "
            ".pth/finder in site-packages.",
            file=sys.stderr,
        )
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    else:
        raise  # a REAL install break must red the suite
