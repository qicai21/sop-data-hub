"""Multiply two scalars. None / pending_review propagates."""
from ops_hub.calc.interpreter import PENDING

NAME = "multiply"


def execute(node, ctx, evaluate):
    lhs = evaluate(node["lhs"], ctx)
    rhs = evaluate(node["rhs"], ctx)
    if lhs is None or rhs is None:
        return None
    if lhs == PENDING or rhs == PENDING:
        return PENDING
    try:
        return float(lhs) * float(rhs)
    except (ValueError, TypeError):
        return None
