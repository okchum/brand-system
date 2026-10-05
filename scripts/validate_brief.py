"""Small standard-library validator for assets/brief.schema.json."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPES = {
    "null": lambda v: v is None,
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
}

def validate(value, schema, path="$", root=None):
    root = root or schema
    errors = []
    if "$ref" in schema:
        target = root["$defs"][schema["$ref"].split("/")[-1]]
        return validate(value, target, path, root)
    typ = schema.get("type")
    types = typ if isinstance(typ, list) else [typ] if typ else []
    if types and not any(TYPES.get(t, lambda v: False)(value) for t in types):
        return [f"{path}: expected {types}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not allowed")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]: errors.append(f"{path}: too short")
        if "pattern" in schema and not re.search(schema["pattern"], value): errors.append(f"{path}: does not match pattern")
    if TYPES["integer"](value) and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: below minimum {schema['minimum']}")
    if "anyOf" in schema and not any(not validate(value, option, path, root) for option in schema["anyOf"]):
        errors.append(f"{path}: matches none of anyOf")
    if "oneOf" in schema:
        matched = sum(1 for option in schema["oneOf"] if not validate(value, option, path, root))
        if matched != 1: errors.append(f"{path}: must match exactly one of oneOf (matched {matched})")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value: errors.append(f"{path}.{key}: required")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in schema.get("properties", {}): errors.append(f"{path}.{key}: unexpected property")
        for key, subschema in schema.get("properties", {}).items():
            if key in value: errors.extend(validate(value[key], subschema, f"{path}.{key}", root))
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]: errors.append(f"{path}: too many items")
        if "minItems" in schema and len(value) < schema["minItems"]: errors.append(f"{path}: too few items")
        for i, item in enumerate(value): errors.extend(validate(item, schema.get("items", {}), f"{path}[{i}]", root))
        if schema.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in value}) != len(value): errors.append(f"{path}: items must be unique")
    return errors

def validate_file(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "assets/brief.schema.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read brief/schema: {exc}"]
    return validate(data, schema)
