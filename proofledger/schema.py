"""Minimal JSON Schema validator (standard library only) for the files in proofledger/schemas/.

Supports the subset those schemas use: type, enum, const, required, properties,
additionalProperties (bool), items, minItems, maxItems, minLength, maxLength, minimum, maximum,
pattern, oneOf and local $ref ("#/$defs/name").
"""
import json
import re
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).parent / "schemas"
_T = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


@lru_cache(None)
def load(name):
    return json.loads((DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _is(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _T[t])


def errors(value, schema, root=None, path="$"):
    root = root or schema
    if "$ref" in schema:
        return errors(value, root["$defs"][schema["$ref"].split("/")[-1]], root, path)
    if "oneOf" in schema:
        ok = [s for s in schema["oneOf"] if not errors(value, s, root, path)]
        return [] if len(ok) == 1 else [f"{path}: matches {len(ok)} of oneOf"]
    out = []
    t = schema.get("type")
    if t and not any(_is(value, x) for x in ([t] if isinstance(t, str) else t)):
        return [f"{path}: expected {t}"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} not in enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 10 ** 9):
            out.append(f"{path}: bad length")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: does not match pattern")
    if _is(value, "number"):
        if value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf")):
            out.append(f"{path}: out of range")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10 ** 9):
            out.append(f"{path}: bad item count")
        if "items" in schema:
            for n, x in enumerate(value):
                out += errors(x, schema["items"], root, f"{path}[{n}]")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}: missing {k}")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                out += errors(v, props[k], root, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: unexpected {k}")
    return out


def validate(value, name):
    """Return a list of problems for value against schemas/<name>.schema.json (empty = valid)."""
    return errors(value, load(name))
