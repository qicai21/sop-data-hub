"""Auto-discover ops and sources at import time.

A new op = drop ``ops/<name>.py`` with module-level::

    NAME = "my_op"
    def execute(node, ctx, evaluate): ...

A new source = drop ``sources/<name>.py`` with::

    NAME = "my_source"
    def fetch(ctx): ...

The scan happens once on first import of this module.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Callable

OP_REGISTRY: dict[str, Callable[..., Any]] = {}
SOURCE_REGISTRY: dict[str, Callable[[dict], list]] = {}


def _scan(package_name: str, target: dict, callable_attr: str) -> None:
    pkg = importlib.import_module(package_name)
    for _, modname, _ in pkgutil.iter_modules(pkg.__path__):
        if modname.startswith("_"):
            continue
        mod = importlib.import_module(f"{package_name}.{modname}")
        name = getattr(mod, "NAME", None)
        handler = getattr(mod, callable_attr, None)
        if not name or not handler:
            continue
        if name in target:
            raise RuntimeError(f"calc: duplicate {callable_attr} '{name}' in {modname}")
        target[name] = handler


_scan("ops_hub.calc.ops", OP_REGISTRY, "execute")
_scan("ops_hub.calc.sources", SOURCE_REGISTRY, "fetch")


def fetch_source(name: str, ctx: dict) -> list:
    src = SOURCE_REGISTRY.get(name)
    if src is None:
        raise ValueError(f"calc: unknown source '{name}'. "
                         f"Available: {sorted(SOURCE_REGISTRY)}")
    return src(ctx)
