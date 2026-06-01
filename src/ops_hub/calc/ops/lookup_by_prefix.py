"""Find the first prefix in ``prefix_map`` that the input string starts with.

Used e.g. to derive a standard-loading-weight class from car_model strings:
    C70  -> 70
    C70E -> 70
    C64K -> 61
    C62EK -> 60

Longer prefixes are tried first so 'C70E' would beat 'C7' if both were present.
"""
NAME = "lookup_by_prefix"


def execute(node, ctx, evaluate):
    value = evaluate(node["value"], ctx)
    on_miss = node.get("on_miss")
    if value is None:
        return on_miss
    s = str(value)
    prefix_map: dict = node.get("prefix_map", {})
    for prefix in sorted(prefix_map.keys(), key=len, reverse=True):
        if s.startswith(prefix):
            return prefix_map[prefix]
    return on_miss
