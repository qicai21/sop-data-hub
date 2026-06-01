"""Tiny expression DSL for SOP-driven calculations.

Goal: project YAML can declare derivations (e.g. shipped_weight per wagon)
as nested op nodes that the kernel evaluates without per-project code.

Public API:
    evaluate(expr, ctx)         — evaluate any DSL node
    fetch_source(name, ctx)     — fetch an aggregator data source by name

Adding a new op = drop ``ops/<name>.py`` exposing NAME + execute(node, ctx, evaluate).
Adding a new source = drop ``sources/<name>.py`` exposing NAME + fetch(ctx).
Both are auto-registered at import time via ``registry.py``.
"""
from sop_hub.calc.interpreter import evaluate, resolve_field
from sop_hub.calc.registry import OP_REGISTRY, SOURCE_REGISTRY, fetch_source

__all__ = ["evaluate", "resolve_field", "OP_REGISTRY", "SOURCE_REGISTRY", "fetch_source"]
