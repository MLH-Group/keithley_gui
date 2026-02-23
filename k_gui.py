from __future__ import annotations

import collections
import collections.abc
import sys


def _apply_visa_import_compat() -> None:
    """Alias legacy visa module names to pyvisa when available."""
    try:
        import pyvisa  # type: ignore[import-not-found]
    except Exception:
        return

    if "visa" not in sys.modules:
        sys.modules["visa"] = pyvisa
    if "Visa" not in sys.modules:
        sys.modules["Visa"] = pyvisa


def _apply_py311_collections_compat() -> None:
    """Backfill removed collections aliases needed by older dependencies."""
    aliases = (
        "Iterator",
        "Iterable",
        "Mapping",
        "MutableMapping",
        "Sequence",
        "Callable",
    )
    for name in aliases:
        if not hasattr(collections, name):
            setattr(collections, name, getattr(collections.abc, name))


_apply_py311_collections_compat()
_apply_visa_import_compat()

from gui import ArbitrarySweeperGUI, main

__all__ = ["ArbitrarySweeperGUI", "main"]

if __name__ == "__main__":
    main()
