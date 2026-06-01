"""Cast a value to float. None / empty / unparseable → None."""
NAME = "to_float"


def execute(node, ctx, evaluate):
    v = evaluate(node["value"], ctx)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
