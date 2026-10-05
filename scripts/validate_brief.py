"""Small standard-library validator for assets/brief.schema.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def validate(value, schema, path="$", root=None):
    root = root or schema
    errors = []
    if "$ref" in schema:
        target = root["$defs"][schema["$ref"].split("/")[-1]]
        return validate(value, target, path, root)
    typ = schema.get("type")
    types = typ if isinstance(typ, list) else [typ] if typ else []
    if types and not any((t == "null" and value is None) or (t == "object" and isinstance(value, dict)) or
                         (t == "array" and isinstance(value, list)) or (t == "string" and isinstance(value, str)) or
                         (t == "boolean" and isinstance(value, bool)) for t in types):
        return [f"{path}: expected {types}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not allowed")
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
