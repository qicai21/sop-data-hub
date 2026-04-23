import copy
import json


def read_json(value):
    if isinstance(value, str):
        return json.loads(value)
    return copy.deepcopy(value)


def write_json(value, pretty=True):
    indent = 2 if pretty else None
    return json.dumps(value, ensure_ascii=False, indent=indent)


def to_searchable_text(value):
    chunks = []
    _walk(value, chunks)
    return " ".join(chunks).strip()


def _walk(value, chunks):
    if value is None:
        return

    if isinstance(value, list):
        for item in value:
            _walk(item, chunks)
        return

    if isinstance(value, dict):
        for key, item in value.items():
            chunks.append(str(key))
            _walk(item, chunks)
        return

    chunks.append(str(value))
