# Nairobi-GIS

GIS data for Nairobi County — includes tools to convert GeoJSON data into SQL
database format for use with SQLite, PostgreSQL/PostGIS, or any generic
SQL-compatible database.

---

## Contents

```
.
├── data/
│   ├── nairobi_wards.geojson   # Nairobi ward boundaries (Polygon features)
│   └── nairobi_poi.geojson     # Nairobi points of interest (Point features)
├── geojson_to_sql.py           # Conversion script
├── requirements.txt            # Python dependencies
└── tests/
    └── test_geojson_to_sql.py  # Test suite
```

---

## Requirements

- Python 3.10+
- [pytest](https://docs.pytest.org/) (for running tests)

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Usage

### Command-line

```bash
python geojson_to_sql.py <input.geojson> [options]
```

| Option | Description |
|---|---|
| `-o FILE` / `--output FILE` | Write SQL to `FILE` instead of stdout |
| `--table NAME` | Override the target table name (defaults to file stem) |
| `--dialect sqlite\|postgresql\|generic` | SQL dialect (default: `sqlite`) |

#### Examples

Convert ward boundaries to SQLite SQL (printed to stdout):

```bash
python geojson_to_sql.py data/nairobi_wards.geojson
```

Save points-of-interest to a file using the PostgreSQL/PostGIS dialect:

```bash
python geojson_to_sql.py data/nairobi_poi.geojson \
    --dialect postgresql \
    -o nairobi_poi.sql
```

Use a custom table name:

```bash
python geojson_to_sql.py data/nairobi_wards.geojson --table wards
```

### Python API

```python
from geojson_to_sql import convert

# Returns the full SQL script as a string
sql = convert("data/nairobi_wards.geojson", dialect="sqlite")
print(sql)

# Write directly to a file
with open("output.sql", "w") as f:
    f.write(convert("data/nairobi_poi.geojson", dialect="postgresql"))
```

---

## Output format

### SQLite / generic

Geometry is stored as a **WKT** (Well-Known Text) string in a `TEXT` column:

```sql
CREATE TABLE IF NOT EXISTS nairobi_wards (
    id INTEGER PRIMARY KEY,
    ward_id INTEGER,
    ward_name TEXT,
    subcounty TEXT,
    county TEXT,
    area_sqkm REAL,
    population INTEGER,
    geometry TEXT
);

INSERT INTO nairobi_wards (...) VALUES (1, 1, 'Westlands', ..., 'POLYGON(...)');
```

### PostgreSQL / PostGIS

Geometry is stored in a `GEOMETRY` column using `ST_GeomFromText()` with SRID 4326:

```sql
CREATE TABLE IF NOT EXISTS nairobi_poi (
    id INTEGER PRIMARY KEY,
    ...
    geometry GEOMETRY
);

INSERT INTO nairobi_poi (...) VALUES (..., ST_GeomFromText('POINT(36.87 -1.37)', 4326));
```

> **Note:** Enable the PostGIS extension once per database before importing:
> ```sql
> CREATE EXTENSION IF NOT EXISTS postgis;
> ```

---

## Supported geometry types

| GeoJSON type | WKT output |
|---|---|
| `Point` | `POINT(x y)` |
| `MultiPoint` | `MULTIPOINT(...)` |
| `LineString` | `LINESTRING(...)` |
| `MultiLineString` | `MULTILINESTRING(...)` |
| `Polygon` | `POLYGON(...)` |
| `MultiPolygon` | `MULTIPOLYGON(...)` |
| `GeometryCollection` | `GEOMETRYCOLLECTION(...)` |

---

## Running tests

```bash
python -m pytest tests/ -v
```
