"""SQLite schema + connection for Hermes.

Single-file DB at data/hermes.db. Schema is idempotent — calling init_db()
on an existing DB is a no-op for unchanged tables.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "hermes.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurants (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    city         TEXT NOT NULL,
    zip          TEXT,
    cuisine      TEXT,
    yelp_slug    TEXT UNIQUE,
    gmaps_id     TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
    source        TEXT NOT NULL CHECK (source IN ('yelp','gmaps')),
    review_date   TEXT,            -- ISO YYYY-MM-DD, nullable if unparseable
    rating        INTEGER,         -- 1..5, nullable if missing
    text          TEXT NOT NULL,
    text_hash     TEXT NOT NULL,   -- sha1(text), for idempotent reloads
    scraped_at    TEXT NOT NULL,   -- ISO timestamp of load
    UNIQUE (restaurant_id, source, text_hash)
);

CREATE INDEX IF NOT EXISTS idx_reviews_date     ON reviews(review_date);
CREATE INDEX IF NOT EXISTS idx_reviews_rest     ON reviews(restaurant_id);

CREATE TABLE IF NOT EXISTS brand_menu_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand           TEXT NOT NULL,
    location        TEXT,
    category        TEXT NOT NULL,    -- base/protein/salsa/topping/dip/dish/...
    item            TEXT NOT NULL,    -- the ingredient or dish name
    ingredients_text TEXT,            -- nullable; sweetgreen-style dish description
    available       INTEGER NOT NULL DEFAULT 1,  -- 0 = out of stock at the captured store
    UNIQUE (brand, category, item)
);

CREATE TABLE IF NOT EXISTS trends (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    term       TEXT NOT NULL,
    geo        TEXT NOT NULL,        -- US-CA, US-NY, ...
    timeframe  TEXT NOT NULL,        -- e.g. 'today 12-m'
    avg_12m    REAL,
    recent_4w  REAL,
    peak       REAL,
    trend      TEXT,                 -- rising/falling/flat
    scraped_at TEXT NOT NULL,
    UNIQUE (term, geo, timeframe)
);

CREATE TABLE IF NOT EXISTS flavor_mentions (
    review_id  INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    flavor     TEXT NOT NULL,
    count      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (review_id, flavor)
);

CREATE INDEX IF NOT EXISTS idx_flavor_mentions_flavor ON flavor_mentions(flavor);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
