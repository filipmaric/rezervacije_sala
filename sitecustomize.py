"""Make the bundled virtualenv available when running from the repo root.

This lets `pytest` and `python app.py` work even if the caller forgot to
activate `venv/`, as long as the checked-in virtualenv exists locally.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _add_bundled_venv_site_packages() -> None:
    if importlib.util.find_spec("flask_login") is not None:
        return

    repo_root = Path(__file__).resolve().parent
    for site_packages in (
        repo_root / "venv" / "lib" / "python3.10" / "site-packages",
        repo_root / "venv" / "lib" / "python3.11" / "site-packages",
    ):
        if site_packages.exists():
            sys.path.insert(0, str(site_packages))
            return


_add_bundled_venv_site_packages()
