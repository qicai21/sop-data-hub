"""Exact-key lookup with on_miss fallback.

Robust against str/int/float key forms (table is YAML so keys are usually str;
inputs from DB may be float like 70.00 or int 70).
"""
NAME = "lookup"


def execute(node, ctx, evaluate):
    key = evaluate(node["key"], ctx)
    table = node.get("table", {})
    on_miss = node.get("on_miss")

    if key is None:
        return on_miss

    # Build candidate keys: original + several string forms
    candidates: list = [key]
    if isinstance(key, bool):
        pass  # bool is also int; skip numeric coercions
    elif isinstance(key, (int, float)):
        candidates.append(str(key))
        try:
            f = float(key)
            if f.is_integer():
                candidates.append(str(int(f)))
                candidates.append(f"{int(f)}.00")
            candidates.append(f"{f:.2f}")
        except (ValueError, TypeError):
            pass
    elif isinstance(key, str):
        try:
            f = float(key)
            candidates.append(f)
            if f.is_integer():
                candidates.append(int(f))
                candidates.append(str(int(f)))
            candidates.append(f"{f:.2f}")
        except (ValueError, TypeError):
            pass

    for c in candidates:
        if c in table:
            return table[c]
    return on_miss
