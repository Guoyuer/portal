"""Generate Zod schemas from selected TypedDicts in ``etl/types.py``.

Keeps the frontend's Zod schemas in ``src/lib/schemas/_generated.ts`` in
sync with the Python source-of-truth. Previously hand-written, which
invited drift whenever the Python side added/renamed a field.

## How schemas are specified

Each generated Zod schema is declared as a ``ViewSpec`` — a declarative
mapping from a source TypedDict to a projected Zod shape. The spec
supports four shape transformations between Python and TS:

1. **1-for-1 mirror** — all fields kept, names auto-converted
   ``snake_case → camelCase``. Declare with ``source="X", include=None``.
2. **Subset (drop fields)** — declare ``include={"field1": None, ...}``;
   unlisted source fields are dropped. ``None`` means "auto camelCase".
   A string value overrides the output field name (rename).
3. **Rename** — declare ``include={"src_name": "outName"}``. Inherits
   the source type; only the name differs on the TS side.
4. **Extra fields** — fields not present in the Python TypedDict but
   added by downstream ingestion (e.g. ``is_retirement`` set while building
   local SQLite rows). Declare with ``extra={"is_retirement": "bool"}``.
   Type must be one of the Python scalars (``str``, ``float``,
   ``int``, ``bool``).

The generated file's schemas appear in the order the specs are
declared so inter-schema references (e.g. ``AllocationRowSchema``
references ``TickerDetailSchema``) resolve top-down.

## Supported Python annotations

``str`` / ``float`` / ``int`` / ``bool``, ``list[X]``, ``dict[str, X]``,
``NotRequired[X]`` → ``.optional()``, ``X | None`` → ``.nullable()``,
references to any other TypedDict in the known set.

Unsupported types (e.g. ``tuple[object, ...]``) raise ``ValueError`` —
you can't silently drop a field.

## Usage

    python tools/gen_zod.py                     # print to stdout
    python tools/gen_zod.py --check PATH        # CI mode: exit 1 on drift
    python tools/gen_zod.py --write PATH        # write to PATH
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

_PYTHON_TO_ZOD_SCALAR: dict[str, str] = {
    "str": "z.string()",
    "float": "z.number()",
    "int": "z.number().int()",
    "bool": "z.boolean()",
}


@dataclass(frozen=True)
class ViewSpec:
    """Declarative Python TypedDict → Zod schema projection.

    Attributes:
        output: Name of the emitted Zod schema (``{output}Schema``).
        source: Name of the source TypedDict in ``etl/types.py``.
        include: If set, only these source fields are kept. Value is the
            output (camelCase) name, or ``None`` to derive it automatically.
            If ``None``, all source fields are emitted with auto-camelCase.
        extra: Fields absent from the TypedDict but present in the Python
            row written to SQLite/exported JSON. Map output name → scalar Python type.
    """
    output: str
    source: str
    include: dict[str, str | None] | None = None
    extra: dict[str, str] = field(default_factory=dict)


# Order matters: child schemas must appear before parents that reference them.
_SPECS: tuple[ViewSpec, ...] = (
    # 1-for-1 mirrors
    ViewSpec(output="TickerDetail", source="TickerDetail"),
    ViewSpec(output="AllocationRow", source="AllocationRow"),

    # Exporter view projections (subsets ± renames)
    ViewSpec(
        output="FidelityTxn",
        source="FidelityTransaction",
        include={
            "date": "runDate",
            "action_type": None,
            "symbol": None,
            "amount": None,
            "quantity": None,
            "price": None,
        },
    ),
    ViewSpec(
        output="QianjiTxn",
        source="QianjiRecord",
        include={
            "date": None,
            "type": None,
            "category": None,
            "amount": None,
            "account_to": None,
        },
        extra={"is_retirement": "bool"},
    ),
    ViewSpec(
        output="RobinhoodTxn",
        source="RobinhoodTransaction",
        include={
            "txn_date": None,
            "action_kind": None,
            "ticker": None,
            "quantity": None,
            "amount_usd": None,
        },
    ),
    ViewSpec(
        output="EmpowerContribution",
        source="EmpowerContribution",
        include={
            "date": None,
            "amount": None,
            "ticker": None,
        },
    ),
)


def _snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def _render_type(annotation: ast.expr, known_typeddicts: set[str]) -> str:
    """Translate a Python annotation AST node into a Zod expression."""
    if isinstance(annotation, ast.Name):
        if annotation.id in _PYTHON_TO_ZOD_SCALAR:
            return _PYTHON_TO_ZOD_SCALAR[annotation.id]
        if annotation.id in known_typeddicts:
            return f"{annotation.id}Schema"
        msg = f"Unsupported bare type: {annotation.id}"
        raise ValueError(msg)

    if isinstance(annotation, ast.Subscript):
        container = annotation.value.id if isinstance(annotation.value, ast.Name) else ""
        if container == "list":
            inner = _render_type(annotation.slice, known_typeddicts)
            return f"z.array({inner})"
        if container == "dict" and (
            isinstance(annotation.slice, ast.Tuple) and len(annotation.slice.elts) == 2
        ):
            v_type = _render_type(annotation.slice.elts[1], known_typeddicts)
            return f"z.record(z.string(), {v_type})"
        if container == "NotRequired":
            inner = _render_type(annotation.slice, known_typeddicts)
            return f"{inner}.optional()"

    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left_none = isinstance(annotation.left, ast.Constant) and annotation.left.value is None
        right_none = isinstance(annotation.right, ast.Constant) and annotation.right.value is None
        if right_none:
            return f"{_render_type(annotation.left, known_typeddicts)}.nullable()"
        if left_none:
            return f"{_render_type(annotation.right, known_typeddicts)}.nullable()"

    msg = f"Unsupported annotation: {ast.dump(annotation)}"
    raise ValueError(msg)


def _extract_typeddicts(source: str) -> dict[str, list[tuple[str, ast.expr]]]:
    """Parse the module; return {class_name: [(field_name, annotation)]}."""
    tree = ast.parse(source)
    out: dict[str, list[tuple[str, ast.expr]]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {b.id for b in node.bases if isinstance(b, ast.Name)}
        if "TypedDict" not in base_names:
            continue
        fields: list[tuple[str, ast.expr]] = []
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.append((stmt.target.id, stmt.annotation))
        out[node.name] = fields
    return out


def _render_schema(spec: ViewSpec, dicts: dict[str, list[tuple[str, ast.expr]]],
                   known: set[str]) -> list[str]:
    """Return the lines of one Zod schema + its inferred TS type."""
    source_fields = dict(dicts[spec.source])  # keep order via dict (3.7+)

    selected: list[tuple[str, str | None]]
    if spec.include is None:
        selected = [(src, None) for src in source_fields]
    else:
        unknown = set(spec.include) - set(source_fields)
        if unknown:
            msg = f"{spec.output}: include names not in {spec.source}: {sorted(unknown)}"
            raise ValueError(msg)
        selected = list(spec.include.items())

    lines = [f"export const {spec.output}Schema = z.object({{"]
    for src_name, out_override in selected:
        out_name = out_override or _snake_to_camel(src_name)
        zod = _render_type(source_fields[src_name], known)
        lines.append(f"  {out_name}: {zod},")
    for extra_name, py_type in spec.extra.items():
        if py_type not in _PYTHON_TO_ZOD_SCALAR:
            msg = f"{spec.output}.extra[{extra_name}]: unsupported type {py_type!r}"
            raise ValueError(msg)
        zod = _PYTHON_TO_ZOD_SCALAR[py_type]
        lines.append(f"  {_snake_to_camel(extra_name)}: {zod},")
    lines.append("});")
    lines.append("")
    lines.append(f"export type {spec.output} = z.infer<typeof {spec.output}Schema>;")
    lines.append("")
    return lines


def render_zod(types_py: Path) -> str:
    source = types_py.read_text(encoding="utf-8")
    dicts = _extract_typeddicts(source)

    missing = {s.source for s in _SPECS} - set(dicts)
    if missing:
        msg = f"Missing TypedDict(s) in types.py: {sorted(missing)}"
        raise ValueError(msg)

    # Known names used for cross-references — only schemas we emit count.
    # Source TypedDict names become referenceable under their output name.
    known = {s.output for s in _SPECS}
    # Also allow intra-generator references by source name (e.g. AllocationRow
    # references TickerDetailSchema; source and output happen to coincide).
    known |= {s.source for s in _SPECS if s.source == s.output}

    lines = [
        "// Auto-generated by pipeline/tools/gen_zod.py from pipeline/etl/types.py.",
        "// DO NOT EDIT BY HAND — rerun the generator to update.",
        "",
        'import { z } from "zod";',
        "",
    ]
    for spec in _SPECS:
        lines.extend(_render_schema(spec, dicts, known))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--types", type=Path, default=None)
    parser.add_argument("--write", type=Path, default=None)
    parser.add_argument("--check", type=Path, default=None)
    args = parser.parse_args(argv)

    types_py = args.types or Path(__file__).resolve().parent.parent / "etl" / "types.py"
    rendered = render_zod(types_py)

    if args.check is not None:
        existing = args.check.read_text(encoding="utf-8") if args.check.exists() else ""
        if existing.strip() != rendered.strip():
            print("✗ Generated Zod differs from", args.check, file=sys.stderr)
            print("Re-run: python tools/gen_zod.py --write", args.check, file=sys.stderr)
            return 1
        print("✓", args.check, "matches generator output")
        return 0

    if args.write is not None:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(rendered, encoding="utf-8")
        print("→ wrote", args.write)
        return 0

    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
