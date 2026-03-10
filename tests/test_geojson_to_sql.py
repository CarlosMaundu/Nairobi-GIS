"""Tests for geojson_to_sql.py"""

import json
import os
import sqlite3
import tempfile

import pytest

from geojson_to_sql import (
    _infer_sql_type,
    _safe_identifier,
    _sql_literal,
    build_create_table,
    build_insert,
    convert,
    geometry_to_wkt,
    infer_schema,
)


# ---------------------------------------------------------------------------
# geometry_to_wkt
# ---------------------------------------------------------------------------

class TestGeometryToWkt:
    def test_point(self):
        geom = {"type": "Point", "coordinates": [36.82, -1.28]}
        assert geometry_to_wkt(geom) == "POINT(36.82 -1.28)"

    def test_multipoint(self):
        geom = {"type": "MultiPoint", "coordinates": [[0.0, 1.0], [2.0, 3.0]]}
        assert geometry_to_wkt(geom) == "MULTIPOINT((0.0 1.0), (2.0 3.0))"

    def test_linestring(self):
        geom = {"type": "LineString", "coordinates": [[0, 0], [1, 1], [2, 2]]}
        assert geometry_to_wkt(geom) == "LINESTRING(0 0, 1 1, 2 2)"

    def test_multilinestring(self):
        geom = {
            "type": "MultiLineString",
            "coordinates": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]],
        }
        assert geometry_to_wkt(geom) == "MULTILINESTRING((0 0, 1 1), (2 2, 3 3))"

    def test_polygon_no_holes(self):
        geom = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        }
        assert geometry_to_wkt(geom) == "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"

    def test_polygon_with_hole(self):
        geom = {
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]],
            ],
        }
        wkt = geometry_to_wkt(geom)
        assert wkt.startswith("POLYGON(")
        assert wkt.count("(") == 3  # outer ring + hole

    def test_multipolygon(self):
        geom = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]],
            ],
        }
        wkt = geometry_to_wkt(geom)
        assert wkt.startswith("MULTIPOLYGON(")

    def test_geometry_collection(self):
        geom = {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Point", "coordinates": [0, 0]},
                {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            ],
        }
        wkt = geometry_to_wkt(geom)
        assert wkt.startswith("GEOMETRYCOLLECTION(")
        assert "POINT" in wkt
        assert "LINESTRING" in wkt

    def test_none_geometry(self):
        assert geometry_to_wkt(None) is None

    def test_unknown_type_returns_none(self):
        geom = {"type": "UnknownType", "coordinates": []}
        assert geometry_to_wkt(geom) is None


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

class TestSafeIdentifier:
    def test_lowercase_passthrough(self):
        assert _safe_identifier("ward_id") == "ward_id"

    def test_uppercase_lowercased(self):
        assert _safe_identifier("WardID") == "wardid"

    def test_spaces_replaced(self):
        assert _safe_identifier("ward name") == "ward_name"

    def test_special_chars_replaced(self):
        assert _safe_identifier("ward-name!") == "ward_name_"

    def test_slash_replaced(self):
        assert _safe_identifier("Parklands/Highridge") == "parklands_highridge"


class TestSqlLiteral:
    def test_none(self):
        assert _sql_literal(None) == "NULL"

    def test_bool_true(self):
        assert _sql_literal(True) == "TRUE"

    def test_bool_false(self):
        assert _sql_literal(False) == "FALSE"

    def test_integer(self):
        assert _sql_literal(42) == "42"

    def test_float(self):
        assert _sql_literal(3.14) == "3.14"

    def test_string(self):
        assert _sql_literal("hello") == "'hello'"

    def test_string_with_single_quote(self):
        assert _sql_literal("it's") == "'it''s'"


class TestInferSqlType:
    def test_all_integers(self):
        assert _infer_sql_type([1, 2, 3], "sqlite") == "INTEGER"

    def test_all_floats(self):
        assert _infer_sql_type([1.1, 2.2], "sqlite") == "REAL"

    def test_floats_postgresql(self):
        assert _infer_sql_type([1.1, 2.2], "postgresql") == "DOUBLE PRECISION"

    def test_booleans(self):
        assert _infer_sql_type([True, False], "sqlite") == "BOOLEAN"

    def test_strings(self):
        assert _infer_sql_type(["a", "b"], "sqlite") == "TEXT"

    def test_mixed_int_float(self):
        assert _infer_sql_type([1, 2.5], "sqlite") == "REAL"

    def test_all_none(self):
        assert _infer_sql_type([None, None], "sqlite") == "TEXT"

    def test_empty(self):
        assert _infer_sql_type([], "sqlite") == "TEXT"


# ---------------------------------------------------------------------------
# Schema inference
# ---------------------------------------------------------------------------

class TestInferSchema:
    def _make_features(self, props_list):
        return [{"properties": p} for p in props_list]

    def test_basic_schema(self):
        features = self._make_features([{"name": "A", "value": 1}])
        schema = infer_schema(features, "sqlite")
        assert "name" in schema
        assert "value" in schema
        assert schema["value"] == "INTEGER"
        assert schema["name"] == "TEXT"

    def test_key_sanitised(self):
        features = self._make_features([{"Ward Name": "X"}])
        schema = infer_schema(features, "sqlite")
        assert "ward_name" in schema

    def test_null_properties(self):
        features = [{"properties": None}]
        schema = infer_schema(features, "sqlite")
        assert schema == {}

    def test_missing_properties_key(self):
        features = [{}]
        schema = infer_schema(features, "sqlite")
        assert schema == {}


# ---------------------------------------------------------------------------
# build_create_table / build_insert
# ---------------------------------------------------------------------------

class TestBuildCreateTable:
    def test_sqlite_creates_table(self):
        schema = {"ward_name": "TEXT", "population": "INTEGER"}
        ddl = build_create_table("wards", schema, "sqlite")
        assert "CREATE TABLE IF NOT EXISTS wards" in ddl
        assert "ward_name TEXT" in ddl
        assert "population INTEGER" in ddl
        assert "geometry TEXT" in ddl
        assert "id INTEGER PRIMARY KEY" in ddl

    def test_postgresql_uses_geometry_type(self):
        schema = {"name": "TEXT"}
        ddl = build_create_table("wards", schema, "postgresql")
        assert "geometry GEOMETRY" in ddl

    def test_generic_dialect(self):
        schema = {"name": "TEXT"}
        ddl = build_create_table("wards", schema, "generic")
        assert "CREATE TABLE IF NOT EXISTS wards" in ddl


class TestBuildInsert:
    def _make_feature(self, props, geom=None):
        return {"properties": props, "geometry": geom}

    def test_basic_insert_sqlite(self):
        schema = {"name": "TEXT", "pop": "INTEGER"}
        feature = self._make_feature({"name": "Westlands", "pop": 96006})
        sql = build_insert("wards", 1, feature, schema, "sqlite")
        assert "INSERT INTO wards" in sql
        assert "'Westlands'" in sql
        assert "96006" in sql

    def test_null_geometry_inserts_null(self):
        schema = {"name": "TEXT"}
        feature = self._make_feature({"name": "X"}, geom=None)
        sql = build_insert("wards", 1, feature, schema, "sqlite")
        assert "NULL" in sql

    def test_postgresql_uses_st_geomfromtext(self):
        schema = {"name": "TEXT"}
        geom = {"type": "Point", "coordinates": [36.82, -1.28]}
        feature = self._make_feature({"name": "X"}, geom=geom)
        sql = build_insert("wards", 1, feature, schema, "postgresql")
        assert "ST_GeomFromText(" in sql
        assert "4326" in sql

    def test_row_id_increments(self):
        schema = {"name": "TEXT"}
        feature = self._make_feature({"name": "X"})
        sql1 = build_insert("t", 1, feature, schema, "sqlite")
        sql2 = build_insert("t", 2, feature, schema, "sqlite")
        assert "VALUES (1," in sql1
        assert "VALUES (2," in sql2

    def test_special_chars_in_string_escaped(self):
        schema = {"name": "TEXT"}
        feature = self._make_feature({"name": "O'Brien"})
        sql = build_insert("t", 1, feature, schema, "sqlite")
        assert "O''Brien" in sql


# ---------------------------------------------------------------------------
# End-to-end: convert()
# ---------------------------------------------------------------------------

class TestConvert:
    def _sample_geojson(self, features):
        return {"type": "FeatureCollection", "features": features}

    def _write_geojson(self, data, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_convert_point_geojson(self, tmp_path):
        data = self._sample_geojson(
            [
                {
                    "type": "Feature",
                    "properties": {"poi_id": 1, "name": "Nairobi National Park"},
                    "geometry": {"type": "Point", "coordinates": [36.87, -1.37]},
                }
            ]
        )
        infile = tmp_path / "poi.geojson"
        self._write_geojson(data, infile)
        sql = convert(str(infile))
        assert "CREATE TABLE IF NOT EXISTS poi" in sql
        assert "INSERT INTO poi" in sql
        assert "POINT(36.87 -1.37)" in sql

    def test_convert_polygon_geojson(self, tmp_path):
        data = self._sample_geojson(
            [
                {
                    "type": "Feature",
                    "properties": {"ward_id": 1, "ward_name": "Westlands"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[36.79, -1.26], [36.81, -1.26], [36.81, -1.24], [36.79, -1.24], [36.79, -1.26]]
                        ],
                    },
                }
            ]
        )
        infile = tmp_path / "wards.geojson"
        self._write_geojson(data, infile)
        sql = convert(str(infile))
        assert "CREATE TABLE IF NOT EXISTS wards" in sql
        assert "POLYGON(" in sql

    def test_table_name_defaults_to_file_stem(self, tmp_path):
        data = self._sample_geojson([])
        infile = tmp_path / "my_layer.geojson"
        self._write_geojson(data, infile)
        sql = convert(str(infile))
        assert "CREATE TABLE IF NOT EXISTS my_layer" in sql

    def test_custom_table_name(self, tmp_path):
        data = self._sample_geojson([])
        infile = tmp_path / "layer.geojson"
        self._write_geojson(data, infile)
        sql = convert(str(infile), table="custom_table")
        assert "CREATE TABLE IF NOT EXISTS custom_table" in sql

    def test_postgresql_dialect(self, tmp_path):
        data = self._sample_geojson(
            [
                {
                    "type": "Feature",
                    "properties": {"name": "Test"},
                    "geometry": {"type": "Point", "coordinates": [36.82, -1.28]},
                }
            ]
        )
        infile = tmp_path / "layer.geojson"
        self._write_geojson(data, infile)
        sql = convert(str(infile), dialect="postgresql")
        assert "postgis" in sql.lower()
        assert "ST_GeomFromText(" in sql
        assert "4326" in sql

    def test_multiple_features_generate_multiple_inserts(self, tmp_path):
        data = self._sample_geojson(
            [
                {"type": "Feature", "properties": {"n": i}, "geometry": None}
                for i in range(5)
            ]
        )
        infile = tmp_path / "multi.geojson"
        self._write_geojson(data, infile)
        sql = convert(str(infile))
        assert sql.count("INSERT INTO") == 5

    def test_sqlite_output_is_valid(self, tmp_path):
        """Generated SQLite SQL can actually be executed."""
        data = self._sample_geojson(
            [
                {
                    "type": "Feature",
                    "properties": {"ward_id": 1, "ward_name": "Westlands", "area": 26.4},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[36.79, -1.26], [36.81, -1.26], [36.81, -1.24], [36.79, -1.26]]
                        ],
                    },
                }
            ]
        )
        infile = tmp_path / "wards.geojson"
        self._write_geojson(data, infile)
        sql = convert(str(infile), dialect="sqlite")

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(sql)
        rows = conn.execute("SELECT * FROM wards").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][1] == 1          # ward_id
        assert rows[0][2] == "Westlands"  # ward_name

    def test_empty_features_list(self, tmp_path):
        data = self._sample_geojson([])
        infile = tmp_path / "empty.geojson"
        self._write_geojson(data, infile)
        sql = convert(str(infile))
        assert "CREATE TABLE IF NOT EXISTS empty" in sql
        assert "INSERT" not in sql

    def test_feature_with_null_properties(self, tmp_path):
        data = self._sample_geojson(
            [{"type": "Feature", "properties": None, "geometry": None}]
        )
        infile = tmp_path / "nullprops.geojson"
        self._write_geojson(data, infile)
        sql = convert(str(infile))
        assert "INSERT INTO nullprops" in sql

    def test_output_file_written(self, tmp_path):
        from geojson_to_sql import main
        data = self._sample_geojson(
            [{"type": "Feature", "properties": {"x": 1}, "geometry": None}]
        )
        infile = tmp_path / "layer.geojson"
        self._write_geojson(data, infile)
        outfile = tmp_path / "out.sql"
        rc = main([str(infile), "-o", str(outfile)])
        assert rc == 0
        assert outfile.exists()
        content = outfile.read_text()
        assert "CREATE TABLE" in content


# ---------------------------------------------------------------------------
# CLI: main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_missing_file_returns_nonzero(self):
        from geojson_to_sql import main
        rc = main(["nonexistent.geojson"])
        assert rc == 1

    def test_stdout_output(self, tmp_path, capsys):
        from geojson_to_sql import main
        data = {"type": "FeatureCollection", "features": []}
        infile = tmp_path / "empty.geojson"
        infile.write_text(json.dumps(data))
        rc = main([str(infile)])
        assert rc == 0
        captured = capsys.readouterr()
        assert "CREATE TABLE" in captured.out


# ---------------------------------------------------------------------------
# Integration: real Nairobi data files
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRealDataFiles:
    def _data_path(self, filename):
        return os.path.join(REPO_ROOT, "data", filename)

    def test_nairobi_wards_converts(self):
        path = self._data_path("nairobi_wards.geojson")
        sql = convert(path, dialect="sqlite")
        assert "CREATE TABLE IF NOT EXISTS nairobi_wards" in sql
        assert sql.count("INSERT INTO nairobi_wards") == 5

    def test_nairobi_wards_sqlite_valid(self, tmp_path):
        path = self._data_path("nairobi_wards.geojson")
        sql = convert(path, dialect="sqlite")
        db = tmp_path / "wards.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(sql)
        rows = conn.execute("SELECT * FROM nairobi_wards").fetchall()
        conn.close()
        assert len(rows) == 5

    def test_nairobi_poi_converts(self):
        path = self._data_path("nairobi_poi.geojson")
        sql = convert(path, dialect="sqlite")
        assert "CREATE TABLE IF NOT EXISTS nairobi_poi" in sql
        assert sql.count("INSERT INTO nairobi_poi") == 6

    def test_nairobi_poi_sqlite_valid(self, tmp_path):
        path = self._data_path("nairobi_poi.geojson")
        sql = convert(path, dialect="sqlite")
        db = tmp_path / "poi.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(sql)
        rows = conn.execute("SELECT * FROM nairobi_poi").fetchall()
        conn.close()
        assert len(rows) == 6

    def test_nairobi_wards_postgresql_dialect(self):
        path = self._data_path("nairobi_wards.geojson")
        sql = convert(path, dialect="postgresql")
        assert "ST_GeomFromText(" in sql
        assert "POLYGON(" in sql

    def test_nairobi_poi_postgresql_dialect(self):
        path = self._data_path("nairobi_poi.geojson")
        sql = convert(path, dialect="postgresql")
        assert "ST_GeomFromText(" in sql
        assert "POINT(" in sql
