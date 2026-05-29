#!/usr/bin/env python3
"""Fetch restaurants + embedded reviews per neighborhood via Bright Data's
Google Maps Fast Maps Search dataset (`gd_m8ebnr0q2qlklc02fz`).

Network goes through `curl --resolve` because local AdGuard sinkholes
api.brightdata.com to 0.0.0.0 at the DNS rule level (the AdGuard "disable
protection" toggle does NOT lift rule-level blocks per repo notes).

Output:
    data/maps_raw/<city>_trigger.json    initial response with snapshot_id
    data/maps_raw/<city>_data.json       final business list + embedded reviews

Usage:
    python scripts/fetch_maps_restaurants.py            # all 3 cities
    python scripts/fetch_maps_restaurants.py weho       # one city
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
API_TOKEN = os.environ["API_TOKEN"]
OUT = ROOT / "data" / "maps_raw"
OUT.mkdir(parents=True, exist_ok=True)

DATASET_ID = "gd_m8ebnr0q2qlklc02fz"

NEIGHBORHOODS = {
    "weho": {
        "lat": 34.0836, "lng": -118.3763,
        "label": "West Hollywood (Beverly/La Cienega)",
    },
    "williamsburg": {
        "lat": 40.7081, "lng": -73.9571,
        "label": "Williamsburg (N 6th & Bedford)",
    },
    "mission": {
        "lat": 37.7599, "lng": -122.4148,
        "label": "Mission District (24th & Mission)",
    },
}

ZOOM = 15
KEYWORD = "restaurant"
POLL_INTERVAL = 15
POLL_TIMEOUT = 60 * 30

BD_HOST = "api.brightdata.com"
TRIGGER_URL = f"https://{BD_HOST}/datasets/v3/trigger"
SNAPSHOT_URL = f"https://{BD_HOST}/datasets/v3/snapshot/{{}}"


def resolve_bd_ip() -> str:
    """Bypass AdGuard sinkhole by resolving via Cloudflare DNS."""
    r = subprocess.run(
        ["dig", "+short", "@1.1.1.1", BD_HOST],
        check=True, capture_output=True, text=True,
    )
    ips = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if not ips:
        raise RuntimeError(f"no IPs returned by dig for {BD_HOST}")
    return ips[0]


BD_IP = resolve_bd_ip()


def bd_request(method: str, url: str, *, params: dict | None = None,
               json_body=None) -> tuple[int, str]:
    """curl-backed BD HTTPS call. Returns (status, body_text)."""
    full_url = url
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        full_url = f"{url}?{qs}"
    cmd = [
        "curl", "-sS", "-X", method,
        "--resolve", f"{BD_HOST}:443:{BD_IP}",
        "-H", f"Authorization: Bearer {API_TOKEN}",
        "-H", "Content-Type: application/json",
        "-o", "/dev/stdout",
        "-w", "\n__HTTP_STATUS__:%{http_code}",
        "--max-time", "90",
    ]
    if json_body is not None:
        cmd += ["-d", json.dumps(json_body)]
    cmd.append(full_url)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed (rc={proc.returncode}): {proc.stderr.strip()}")
    raw = proc.stdout
    # Split body from status marker
    marker = "\n__HTTP_STATUS__:"
    idx = raw.rfind(marker)
    if idx == -1:
        raise RuntimeError(f"curl output missing status marker: {raw[-200:]}")
    body = raw[:idx]
    status = int(raw[idx + len(marker):].strip())
    return status, body


def trigger(city_key: str) -> str:
    nbhd = NEIGHBORHOODS[city_key]
    payload = [{
        "country": "US",
        "lat": nbhd["lat"],
        "long": nbhd["lng"],
        "zoom_level": ZOOM,
        "keyword": KEYWORD,
    }]
    params = {
        "dataset_id": DATASET_ID,
        "format": "json",
        "type": "discover_new",
        "discover_by": "location",
    }
    print(f"[{city_key}] triggering for {nbhd['label']} ({nbhd['lat']}, {nbhd['lng']})", flush=True)
    status, body = bd_request("POST", TRIGGER_URL, params=params, json_body=payload)
    if status >= 400:
        raise RuntimeError(f"trigger HTTP {status}: {body[:500]}")
    j = json.loads(body)
    (OUT / f"{city_key}_trigger.json").write_text(json.dumps(j, indent=2))
    sid = j.get("snapshot_id") or j.get("collection_id") or j.get("id")
    if not sid:
        raise RuntimeError(f"no snapshot_id in trigger response: {j}")
    print(f"[{city_key}] snapshot_id = {sid}", flush=True)
    return sid


def poll(city_key: str, snapshot_id: str):
    url = SNAPSHOT_URL.format(snapshot_id)
    started = time.time()
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        status, body = bd_request("GET", url)
        if status == 202:
            print(f"  [{city_key}] {int(elapsed):>4}s — still building (202)", flush=True)
            time.sleep(POLL_INTERVAL)
            elapsed = time.time() - started
            continue
        if status >= 400:
            raise RuntimeError(f"poll HTTP {status}: {body[:500]}")
        # BD returns ready data as JSONL (one JSON object per line), NOT as a
        # JSON array. Try array first, fall back to JSONL.
        body_strip = body.strip()
        if body_strip.startswith("{") and "\n{" in body_strip:
            try:
                records = [json.loads(ln) for ln in body_strip.splitlines() if ln.strip()]
                print(f"  [{city_key}] ready: {len(records)} JSONL records", flush=True)
                return records
            except ValueError as e:
                print(f"  [{city_key}] JSONL parse failed: {e}", flush=True)
        try:
            j = json.loads(body)
        except ValueError:
            print(f"  [{city_key}] non-JSON body len={len(body)} — saving raw", flush=True)
            (OUT / f"{city_key}_data.raw").write_text(body)
            return {"raw_text": body[:5000]}
        st = j.get("status") if isinstance(j, dict) else None
        if isinstance(j, list):
            print(f"  [{city_key}] ready: {len(j)} records", flush=True)
            return j
        if st in ("ready", "completed") or (isinstance(j, dict) and st is None and "data" in j):
            return j
        if st == "failed":
            raise RuntimeError(f"snapshot failed: {j}")
        print(f"  [{city_key}] {int(elapsed):>4}s — status={st!r}", flush=True)
        time.sleep(POLL_INTERVAL)
        elapsed = time.time() - started
    raise TimeoutError(f"[{city_key}] poll timeout after {POLL_TIMEOUT}s")


def summarize(city_key: str, body):
    records = body if isinstance(body, list) else body.get("data", [])
    if not records and isinstance(body, dict):
        for k in ("results", "items", "businesses"):
            if k in body and isinstance(body[k], list):
                records = body[k]
                break
    print(f"  [{city_key}] records: {len(records)}", flush=True)
    if records and isinstance(records[0], dict):
        sample = records[0]
        keys = list(sample.keys())[:25]
        print(f"  [{city_key}] sample keys: {keys}", flush=True)
        for r in records[:3]:
            if not isinstance(r, dict):
                continue
            name = r.get("name") or r.get("title")
            rc = r.get("reviews_count") or r.get("review_count") or r.get("reviews")
            rating = r.get("rating")
            addr = r.get("address") or r.get("formatted_address")
            print(f"    {name}  rating={rating}  reviews={rc}  addr={addr}", flush=True)


def fetch_city(city_key: str):
    sid = trigger(city_key)
    body = poll(city_key, sid)
    out_path = OUT / f"{city_key}_data.json"
    out_path.write_text(json.dumps(body, indent=2))
    print(f"  [{city_key}] saved → {out_path}", flush=True)
    summarize(city_key, body)


def main():
    print(f"BD_IP resolved: {BD_IP}", flush=True)
    targets = [sys.argv[1]] if len(sys.argv) > 1 else list(NEIGHBORHOODS)
    for city_key in targets:
        if city_key not in NEIGHBORHOODS:
            print(f"unknown city: {city_key}")
            continue
        try:
            fetch_city(city_key)
        except Exception as e:
            print(f"[{city_key}] failed: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
