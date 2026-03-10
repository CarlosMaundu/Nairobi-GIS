#!/usr/bin/env python3
"""
geojson_to_sql.py - Convert GeoJSON files to SQL format.

Supports output for:
  - SQLite  (default)
  - PostgreSQL / PostGIS
  - Generic SQL (geometry stored as WKT text)

Usage:
    python geojson_to_sql.py <input.geojson> [options]

Examples:
    python geojson_to_sql.py data/nairobi_wards.geojson
    python geojson_to_sql.py data/nairobi_wards.geojson -o output.sql --dialect postgresql
    python geojson_to_sql.py data/nairobi_poi.geojson --table my_poi --dialect sqlite
"""

import argparse
import json
import os
import re
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _coords_to_wkt_ring(coords: list) -> str:
    """Convert a list of [lon, lat] pairs to a WKT ring string."""
    return ", ".join(f"{c[0]} {c[1]}" for c in coords)


def geometry_to_wkt(geometry: dict | None) -> str | None:
    """Convert a GeoJSON geometry object to a WKT string.

    Supports Point, MultiPoint, LineString, MultiLineString,
    Polygon, MultiPolygon, and GeometryCollection.

    Returns None when *geometry* is None or has an unrecognised type.
    """
    if geometry is None:
        return None

    gtype = geometry.get("type", "")
    coords = geometry.get("coordinates")

    if gtype == "Point":
        return f"POINT({coords[0]} {coords[1]})"

    if gtype == "MultiPoint":
        pts = ", ".join(f"({c[0]} {c[1]})" for c in coords)
        return f"MULTIPOINT({pts})"

    if gtype == "LineString":
        return f"LINESTRING({_coords_to_wkt_ring(coords)})"

    if gtype == "MultiLineString":
        lines = ", ".join(f"({_coords_to_wkt_ring(r)})" for r in coords)
        return f"MULTILINESTRING({lines})"

    if gtype == "Polygon":
        rings = ", ".join(f"({_coords_to_wkt_ring(r)})" for r in coords)
        return f"POLYGON({rings})"

    if gtype == "MultiPolygon":
        polys = []
        for poly in coords:
            rings = ", ".join(f"({_coords_to_wkt_ring(r)})" for r in poly)
            polys.append(f"({rings})")
        return f"MULTIPOLYGON({', '.join(polys)})"

    if gtype == "GeometryCollection":
        geoms = ", ".join(
            geometry_to_wkt(g) or "GEOMETRYCOLLECTION EMPTY"
            for g in geometry.get("geometries", [])
        )
        return f"GEOMETRYCOLLECTION({geoms})"

    return None


# ---------------------------------------------------------------------------
# SQL quoting / escaping helpers
# ---------------------------------------------------------------------------

def _safe_identifier(name: str) -> str:
    """Return a safe, lower-cased SQL identifier (letters, digits, underscores)."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower())


def _escape_string(value: str) -> str:
    """Escape single quotes for SQL string literals."""
    return value.replace("'", "''")


def _sql_literal(value: Any) -> str:
    """Convert a Python value to a SQL literal string."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return f"'{_escape_string(str(value))}'"


# ---------------------------------------------------------------------------
# Schema inference
# ---------------------------------------------------------------------------

def _infer_sql_type(values: list, dialect: str) -> str:
    """Infer SQL column type from a list of sample values."""
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "TEXT"

    if all(isinstance(v, bool) for v in non_null):
        return "BOOLEAN"

    if all(isinstance(v, int) for v in non_null):
        return "INTEGER"

    if all(isinstance(v, (int, float)) for v in non_null):
        return "REAL" if dialect == "sqlite" else "DOUBLE PRECISION"

    return "TEXT"


def infer_schema(features: list, dialect: str) -> dict[str, str]:
    """Return {column_name: sql_type} for all property keys in *features*."""
    column_values: dict[str, list] = {}
    for feature in features:
        for key, value in (feature.get("properties") or {}).items():
            safe_key = _safe_identifier(key)
            column_values.setdefault(safe_key, []).append(value)

    return {col: _infer_sql_type(vals, dialect) for col, vals in column_values.items()}


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------

def _geometry_column_ddl(dialect: str) -> str:
    """Return the DDL fragment for the geometry column."""
    if dialect == "postgresql":
        return "geometry GEOMETRY"
    return "geometry TEXT"


def _geometry_insert_value(wkt: str | None, dialect: str) -> str:
    """Return the SQL expression used to insert a geometry value."""
    if wkt is None:
        return "NULL"
    if dialect == "postgresql":
        return f"ST_GeomFromText('{_escape_string(wkt)}', 4326)"
    return f"'{_escape_string(wkt)}'"


def build_create_table(table: str, schema: dict[str, str], dialect: str) -> str:
    """Return a CREATE TABLE SQL statement."""
    columns = ["id INTEGER PRIMARY KEY"]
    columns += [f"{col} {dtype}" for col, dtype in schema.items()]
    columns.append(_geometry_column_ddl(dialect))

    sep = ",\n    "
    col_defs = sep.join(columns)

    if dialect == "postgresql":
        return (
            f"CREATE TABLE IF NOT EXISTS {table} (\n"
            f"    {col_defs}\n"
            f");\n"
        )
    # SQLite / generic
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n"
        f"    {col_defs}\n"
        f");\n"
    )


def build_insert(
    table: str,
    row_id: int,
    feature: dict,
    schema: dict[str, str],
    dialect: str,
) -> str:
    """Return an INSERT SQL statement for a single GeoJSON feature."""
    props = feature.get("properties") or {}
    geom = feature.get("geometry")
    wkt = geometry_to_wkt(geom)

    col_names = ["id"] + list(schema.keys()) + ["geometry"]
    values = [str(row_id)]
    for col in schema:
        # Reverse-map safe identifier back to original property key
        orig_key = next(
            (k for k in props if _safe_identifier(k) == col),
            col,
        )
        values.append(_sql_literal(props.get(orig_key)))
    values.append(_geometry_insert_value(wkt, dialect))

    cols_str = ", ".join(col_names)
    vals_str = ", ".join(values)
    return f"INSERT INTO {table} ({cols_str}) VALUES ({vals_str});\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert(
    geojson_path: str,
    table: str | None = None,
    dialect: str = "sqlite",
) -> str:
    """Convert a GeoJSON file to SQL statements.

    Parameters
    ----------
    geojson_path:
        Path to the input GeoJSON file.
    table:
        Target SQL table name.  Defaults to the stem of *geojson_path*.
    dialect:
        One of ``"sqlite"`` (default), ``"postgresql"``, or ``"generic"``.

    Returns
    -------
    str
        Complete SQL script as a string.
    """
    with open(geojson_path, encoding="utf-8") as fh:
        data = json.load(fh)

    features: list = data.get("features", [])

    if table is None:
        stem = os.path.splitext(os.path.basename(geojson_path))[0]
        table = _safe_identifier(stem)

    schema = infer_schema(features, dialect)

    lines = []

    if dialect == "postgresql":
        lines.append("-- Enable PostGIS extension (run once per database)\n")
        lines.append("-- CREATE EXTENSION IF NOT EXISTS postgis;\n\n")

    lines.append(build_create_table(table, schema, dialect))
    lines.append("\n")

    for idx, feature in enumerate(features, start=1):
        lines.append(build_insert(table, idx, feature, schema, dialect))

    return "".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a GeoJSON file to SQL INSERT statements."
    )
    parser.add_argument("input", help="Path to the input GeoJSON file.")
    parser.add_argument(
        "-o", "--output",
        help="Path to the output SQL file. Prints to stdout when omitted.",
    )
    parser.add_argument(
        "--table",
        help="Target SQL table name (defaults to the GeoJSON file stem).",
    )
    parser.add_argument(
        "--dialect",
        choices=["sqlite", "postgresql", "generic"],
        default="sqlite",
        help="SQL dialect to target (default: sqlite).",
    )

    args = parser.parse_args(argv)

    if not os.path.isfile(args.input):
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        return 1

    sql = convert(args.input, table=args.table, dialect=args.dialect)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(sql)
        print(f"SQL written to {args.output}")
    else:
        sys.stdout.write(sql)

    return 0


if __name__ == "__main__":
    sys.exit(main())
