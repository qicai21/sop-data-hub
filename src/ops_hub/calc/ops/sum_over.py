"""Aggregator: evaluate ``each`` over every item from ``source``, sum the
numeric results, count items that returned ``pending_review`` or None.

Returns a dict — not a bare number — because the caller needs both the
total and the unresolved count for audit/dashboard display:

    {"value": float, "unresolved": int, "total_items": int, "basis_per_item": [...]}

``basis_per_item`` is a list of {item_key, result, basis} captured for audit,
when node['capture_basis'] is True.
"""
from ops_hub.calc.interpreter import PENDING

NAME = "sum_over"


def execute(node, ctx, evaluate):
    # Deferred import — registry is in the middle of importing us at startup.
    from ops_hub.calc.registry import fetch_source
    source_name = node["source"]
    items = fetch_source(source_name, ctx)
    each_expr = node["each"]
    item_key_field = node.get("item_key_field", "id")
    capture = bool(node.get("capture_basis", True))

    total = 0.0
    unresolved = 0
    basis: list[dict] = []

    for item in items:
        item_ctx = dict(ctx)
        # Bind item under the conventional alias 'wagon' (most common source)
        # plus a generic 'item' for non-wagon sources.
        item_ctx["wagon"] = item
        item_ctx["item"] = item
        result = evaluate(each_expr, item_ctx)
        item_id = item.get(item_key_field) if isinstance(item, dict) else None

        if result is None or result == PENDING:
            unresolved += 1
            if capture:
                basis.append({"item_id": item_id, "result": None,
                              "reason": result or "null"})
            continue

        try:
            num = float(result)
        except (ValueError, TypeError):
            unresolved += 1
            if capture:
                basis.append({"item_id": item_id, "result": result,
                              "reason": "non_numeric"})
            continue

        total += num
        if capture:
            basis.append({"item_id": item_id, "result": num})

    return {
        "value": total,
        "unresolved": unresolved,
        "total_items": len(items),
        "basis_per_item": basis if capture else None,
    }
