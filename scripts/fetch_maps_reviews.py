#!/usr/bin/env python3
"""Fetch chronological reviews per restaurant via BD's Google Maps Reviews
dataset (`gd_luzfs1dn2oa0teb81`). Server-side `days_limit=365` constrains
to last 12 months so we get real velocity data.

Different product from `gd_m8ebnr0q2qlklc02fz` (Fast Maps Search) which only
returns Google's curated top-8 + 3 snippets per business. This one paginates
the full review feed.

Reads URLs from the existing Fast Maps Search dumps (each record has a `url`
field — no place_id→URL conversion needed).

Curl --resolve workaround for the AdGuard sinkhole, same as fetch_maps_restaurants.py.

Usage:
    # Smoke test against 5 restaurants in WeHo
    python scripts/fetch_maps_reviews.py weho --limit 5

    # Full pull across all Pool A + Pool B restaurants in all 3 cities
    python scripts/fetch_maps_reviews.py --all
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hermes.db import DB_PATH  # noqa: E402

load_dotenv(ROOT / ".env")
API_TOKEN = os.environ["API_TOKEN"]
MAPS = ROOT / "data" / "maps_raw"
OUT = ROOT / "data" / "maps_reviews_raw"
OUT.mkdir(parents=True, exist_ok=True)

DATASET_ID = "gd_luzfs1dn2oa0teb81"
DAYS_LIMIT = 365
POLL_INTERVAL = 20
POLL_TIMEOUT = 60 * 60  # 1 hour ceiling — bigger pulls take longer

BD_HOST = "api.brightdata.com"
TRIGGER_URL = f"https://{BD_HOST}/datasets/v3/trigger"
SNAPSHOT_URL = f"https://{BD_HOST}/datasets/v3/snapshot/{{}}"


def resolve_bd_ip() -> str:
    r = subprocess.run(
        ["dig", "+short", "@1.1.1.1", BD_HOST],
        check=True, capture_output=True, text=True,
    )
    ips = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if not ips:
        raise RuntimeError(f"dig returned no IPs for {BD_HOST}")
    return ips[0]


BD_IP = resolve_bd_ip()


def bd_request(method: str, url: str, *, params: dict | None = None,
               json_body=None) -> tuple[int, str]:
    full_url = url
    if params:
        full_url = url + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    cmd = [
        "curl", "-sS", "-X", method,
        "--resolve", f"{BD_HOST}:443:{BD_IP}",
        "-H", f"Authorization: Bearer {API_TOKEN}",
        "-H", "Content-Type: application/json",
        "-o", "/dev/stdout",
        "-w", "\n__HTTP_STATUS__:%{http_code}",
        "--max-time", "120",
    ]
    if json_body is not None:
        cmd += ["-d", json.dumps(json_body)]
    cmd.append(full_url)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl rc={proc.returncode}: {proc.stderr.strip()}")
    raw = proc.stdout
    marker = "\n__HTTP_STATUS__:"
    idx = raw.rfind(marker)
    if idx == -1:
        raise RuntimeError(f"missing status marker in output: {raw[-200:]}")
    return int(raw[idx + len(marker):].strip()), raw[:idx]


def load_url_map() -> dict[str, dict]:
    """Returns {place_id: {url, city_key, name, review_count}} for all Fast Maps
    Search records across the 3 cities."""
    out = {}
    for city_key in ("weho", "williamsburg", "mission"):
        path = MAPS / f"{city_key}_data.json"
        if not path.exists():
            continue
        for rec in json.loads(path.read_text()):
            pid = rec.get("place_id")
            url = rec.get("url")
            if pid and url:
                out[pid] = {
                    "url": url,
                    "city_key": city_key,
                    "name": rec.get("name"),
                    "review_count": rec.get("reviews_count", 0),
                }
    return out


def pool_filter_place_ids(conn, city_key: str | None) -> list[str]:
    """Returns place_ids for restaurants in pool_competitive=1 OR pool_leading=1."""
    q = (
        "SELECT gmaps_id FROM restaurants "
        "WHERE gmaps_id IS NOT NULL "
        "AND (pool_competitive = 1 OR pool_leading = 1)"
    )
    args: tuple = ()
    if city_key:
        city_label = {"weho": "West Hollywood", "williamsburg": "Williamsburg",
                      "mission": "Mission District"}[city_key]
        q += " AND city = ?"
        args = (city_label,)
    return [r[0] for r in conn.execute(q, args).fetchall()]


def trigger_reviews(payload: list[dict]) -> str:
    print(f"[trigger] submitting {len(payload)} URLs with days_limit={DAYS_LIMIT}...", flush=True)
    status, body = bd_request(
        "POST", TRIGGER_URL,
        params={"dataset_id": DATASET_ID, "format": "json"},
        json_body=payload,
    )
    if status >= 400:
        raise RuntimeError(f"trigger HTTP {status}: {body[:500]}")
    j = json.loads(body)
    sid = j.get("snapshot_id") or j.get("collection_id") or j.get("id")
    if not sid:
        raise RuntimeError(f"no snapshot_id in trigger response: {j}")
    print(f"[trigger] snapshot_id = {sid}", flush=True)
    return sid


def poll(snapshot_id: str) -> dict | list:
    url = SNAPSHOT_URL.format(snapshot_id)
    started = time.time()
    while time.time() - started < POLL_TIMEOUT:
        status, body = bd_request("GET", url)
        elapsed = int(time.time() - started)
        if status == 202:
            print(f"  [{elapsed:>4}s] still building (202)", flush=True)
            time.sleep(POLL_INTERVAL); continue
        if status >= 400:
            raise RuntimeError(f"poll HTTP {status}: {body[:500]}")
        body_strip = body.strip()
        if body_strip.startswith("{") and "\n{" in body_strip:
            recs = [json.loads(ln) for ln in body_strip.splitlines() if ln.strip()]
            print(f"  [{elapsed:>4}s] ready — {len(recs)} JSONL records", flush=True)
            return recs
        try:
            j = json.loads(body)
        except ValueError:
            print(f"  [{elapsed:>4}s] non-JSON, len={len(body)} — returning raw", flush=True)
            return {"raw": body}
        st = j.get("status") if isinstance(j, dict) else None
        if isinstance(j, list):
            print(f"  [{elapsed:>4}s] ready — {len(j)} records", flush=True)
            return j
        if st in ("ready", "completed"):
            return j
        if st == "failed":
            raise RuntimeError(f"snapshot failed: {j}")
        print(f"  [{elapsed:>4}s] status={st!r}", flush=True)
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"poll timeout after {POLL_TIMEOUT}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("city", nargs="?", choices=["weho", "williamsburg", "mission"])
    ap.add_argument("--all", action="store_true", help="all 3 cities")
    ap.add_argument("--limit", type=int, default=None, help="cap restaurant count (smoke test)")
    args = ap.parse_args()

    if not args.city and not args.all:
        ap.error("supply a city or --all")

    print(f"BD_IP resolved: {BD_IP}", flush=True)

    url_map = load_url_map()
    print(f"loaded {len(url_map)} place_id→url pairs from Fast Maps Search dumps", flush=True)

    conn = sqlite3.connect(DB_PATH)
    cities = ["weho", "williamsburg", "mission"] if args.all else [args.city]
    target_pids: list[str] = []
    for ck in cities:
        pids = pool_filter_place_ids(conn, ck)
        # Filter to only those we have URLs for
        pids = [p for p in pids if p in url_map]
        print(f"  {ck}: {len(pids)} pool-tagged restaurants with URLs", flush=True)
        target_pids.extend(pids)
    conn.close()

    if args.limit:
        target_pids = target_pids[:args.limit]
        print(f"--limit applied: capping at {len(target_pids)}", flush=True)

    if not target_pids:
        print("nothing to fetch")
        return

    payload = [{"url": url_map[pid]["url"], "days_limit": DAYS_LIMIT} for pid in target_pids]
    snapshot_id = trigger_reviews(payload)

    # Save trigger info
    ts = time.strftime("%Y%m%d_%H%M%S")
    (OUT / f"trigger_{ts}.json").write_text(json.dumps({
        "snapshot_id": snapshot_id,
        "target_pids": target_pids,
        "days_limit": DAYS_LIMIT,
        "trigger_at": ts,
    }, indent=2))

    body = poll(snapshot_id)
    out_path = OUT / f"reviews_{ts}.json"
    out_path.write_text(json.dumps(body, indent=2))
    print(f"saved → {out_path}", flush=True)

    # Quick summary
    if isinstance(body, list):
        print(f"\ntotal review records returned: {len(body)}")
        if body:
            sample = body[0]
            keys = list(sample.keys())[:25] if isinstance(sample, dict) else "?"
            print(f"sample keys: {keys}")
            # group by business
            by_url: dict = {}
            for r in body:
                if isinstance(r, dict):
                    u = r.get("url") or r.get("input_url") or r.get("place_url") or "?"
                    by_url[u] = by_url.get(u, 0) + 1
            print(f"\nreviews per business (top 5):")
            for u, n in sorted(by_url.items(), key=lambda x: -x[1])[:5]:
                print(f"  {n}: {u[:80]}")


if __name__ == "__main__":
    main()
