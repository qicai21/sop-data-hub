"""Return the first ``choices`` entry that resolves to a non-empty value.

Empty values: None, "", "pending_review". Default if all empty.
"""
from ops_hub.calc.interpreter import PENDING

NAME = "coalesce"


def _is_empty(v):
    return v is None or v == "" or v == PENDING


def execute(node, ctx, evaluate):
    for child in node.get("choices", []):
        val = evaluate(child, ctx)
        if not _is_empty(val):
            return val
    return node.get("default")
