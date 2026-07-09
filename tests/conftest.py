"""LHM test bootstrap — import-path belt-and-braces.

The editable install is the real mechanism, but macOS/uv occasionally writes
the ``__editable__.lhm-*.pth`` with the APFS ``UF_HIDDEN`` flag, which
``site.py`` silently SKIPS (instinct: uv-editable-pth-uf-hidden-breaks-import).
Rather than let a filesystem flag red the suite, fall back to a direct
sys.path insert of the package root.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:  # the editable install (normal path)
    import lhm  # noqa: F401
except ModuleNotFoundError:  # UF_HIDDEN .pth skip — fall back to the source tree
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
