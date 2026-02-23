from __future__ import annotations

import collections
import collections.abc


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

from gui import ArbitrarySweeperGUI, main

__all__ = ["ArbitrarySweeperGUI", "main"]

if __name__ == "__main__":
    main()
