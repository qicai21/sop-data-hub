"""Core evaluator. Recursive, no side effects beyond what ops/sources do."""
from __future__ import annotations

from typing import Any

PENDING = "pending_review"


def evaluate(expr: Any, ctx: dict) -> Any:
    """Evaluate a DSL expression in the given context.

    Literal types pass through:
        None, bool, int, float, plain str (without '$').
    Strings starting with '$' are field references resolved against ctx.
    Dicts with an 'op' key dispatch to the op registry.
    Plain dicts (no 'op') and lists are returned unevaluated (tables, params).
    """
    if expr is None or isinstance(expr, (bool, int, float)):
        return expr
    if isinstance(expr, str):
        if expr.startswith("$"):
            return resolve_field(expr[1:], ctx)
        return expr
    if isinstance(expr, list):
        return [evaluate(item, ctx) for item in expr]
    if isinstance(expr, dict):
        if "op" not in expr:
            return expr
        from ops_hub.calc.registry import OP_REGISTRY
        op_name = expr["op"]
        handler = OP_REGISTRY.get(op_name)
        if handler is None:
            raise ValueError(f"calc: unknown op '{op_name}'. "
                             f"Available: {sorted(OP_REGISTRY)}")
        return handler(expr, ctx, evaluate)
    return expr


def resolve_field(path: str, ctx: dict) -> Any:
    """Resolve dotted path against ctx: 'wagon.car_model' → ctx['wagon']['car_model']."""
    cur: Any = ctx
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
    return cur
