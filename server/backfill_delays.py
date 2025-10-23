#!/usr/bin/env python3
"""
Backfill the last 24 hours of TrainAnnouncement data into SQLite for delay regression.

- Writes into regression.db by default (override with env DATABASE).
- Populates stations, journeys, stops.
- Builds a denormalized delays_regression table for modeling.

Usage:
  TRAFIKVERKET_API_KEY=xxxx python backfill_delays_24h.py
  DATABASE=regression.db TRAFIKVERKET_API_KEY=xxxx python backfill_delays_24h.py
"""

import os
import time
import sqlite3
import requests
from datetime import datetime, timedelta, timezone

# --------------------- Config ---------------------
DATABASE = os.environ.get("DATABASE", "regression.db") 
API_KEY = "demokey"
TV_API_URL = "https://api.trafikinfo.trafikverket.se/v2/data.json"

LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))         
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", str(LOOKBACK_DAYS * 24)))
SLICE_HOURS = int(os.environ.get("SLICE_HOURS", "1"))
SLEEP_BETWEEN_CALLS = float(os.environ.get("SLEEP_BETWEEN_CALLS", "0.35"))

if not API_KEY:
    raise SystemExit("ERROR: set TRAFIKVERKET_API_KEY env var")

# --------------------- DB helpers ---------------------
def get_db():
    conn = sqlite3.connect(DATABASE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_schema():
    db = get_db()
    c = db.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS stations (
        station_signature TEXT PRIMARY KEY,
        station_name TEXT,
        lon REAL,
        lat REAL
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS journeys (
        journey_id INTEGER PRIMARY KEY AUTOINCREMENT,
        train_id TEXT NOT NULL,
        journey_date TEXT NOT NULL,
        operator TEXT,
        UNIQUE(train_id, journey_date)
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS stops (
        stop_id INTEGER PRIMARY KEY AUTOINCREMENT,
        journey_id INTEGER NOT NULL,
        sequence_number INTEGER NOT NULL,
        station_signature TEXT NOT NULL,
        scheduled_arrival TEXT,
        actual_arrival TEXT,
        scheduled_departure TEXT,
        actual_departure TEXT,
        delay_minutes INTEGER,
        is_canceled BOOLEAN DEFAULT 0,
        UNIQUE(journey_id, station_signature, sequence_number)
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS delays_regression (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        journey_id INTEGER NOT NULL,
        train_id TEXT NOT NULL,
        journey_date TEXT NOT NULL,
        station_signature TEXT NOT NULL,
        station_name TEXT,
        lon REAL,
        lat REAL,
        advertised_time TEXT NOT NULL,
        activity_type TEXT NOT NULL,
        delay_minutes INTEGER,
        is_canceled BOOLEAN DEFAULT 0,
        hour INTEGER,
        weekday INTEGER,
        prev_delay_minutes INTEGER
    );
    """)
    db.commit()
    db.close()

def parse_iso_like(s):
    """Parse Trafikverket timestamps; drop fractional secs; ALWAYS return UTC-aware."""
    if not s:
        return None
    try:
        if '.' in s:
            s = s.split('.')[0]
        # Normalize 'Z' to +00:00 for fromisoformat
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            # Treat naive timestamps as UTC
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            # Normalize any offset to UTC
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None

def to_iso_z(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

# --------------------- API helpers ---------------------
def tv_post(xml):
    headers = {'Content-Type': 'text/xml'}
    for attempt in range(5):
        try:
            r = requests.post(TV_API_URL, data=xml, headers=headers, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            wait = 1.0 * (attempt + 1)
            print(f"[API] Error attempt {attempt+1}: {e} -> sleeping {wait}s")
            time.sleep(wait)
    raise RuntimeError("API request failed after retries")

def fetch_stations():
    xml = f"""
    <REQUEST>
      <LOGIN authenticationkey="{API_KEY}" />
      <QUERY objecttype="TrainStation" schemaversion="1">
        <FILTER />
        <INCLUDE>LocationSignature</INCLUDE>
        <INCLUDE>AdvertisedLocationName</INCLUDE>
        <INCLUDE>Geometry.WGS84</INCLUDE>
      </QUERY>
    </REQUEST>
    """
    j = tv_post(xml)
    stations = j.get('RESPONSE', {}).get('RESULT', [{}])[0].get('TrainStation', []) or []
    print(f"[Stations] Got {len(stations)} stations")
    db = get_db()
    c = db.cursor()
    for st in stations:
        sig = st.get('LocationSignature')
        name = st.get('AdvertisedLocationName')
        lon, lat = None, None
        wkt = (st.get('Geometry') or {}).get('WGS84')
        if wkt and 'POINT' in wkt:
            parts = wkt.replace('POINT (', '').replace(')', '').split()
            if len(parts) == 2:
                lon, lat = float(parts[0]), float(parts[1])
        c.execute(
            "INSERT OR REPLACE INTO stations(station_signature, station_name, lon, lat) VALUES (?, ?, ?, ?)",
            (sig, name, lon, lat)
        )
    db.commit()
    db.close()

def fetch_announcements_window(start_dt, end_dt):
    start_iso = to_iso_z(start_dt)
    end_iso = to_iso_z(end_dt)
    xml = f"""
    <REQUEST>
      <LOGIN authenticationkey="{API_KEY}" />
      <QUERY objecttype="TrainAnnouncement" schemaversion="1.6" orderby="AdvertisedTimeAtLocation">
        <FILTER>
          <AND>
            <EQ name="Advertised" value="true" />
            <OR>
              <EQ name="ActivityType" value="Ankomst" />
              <EQ name="ActivityType" value="Avgang" />
            </OR>
            <GTE name="AdvertisedTimeAtLocation" value="{start_iso}" />
            <LT  name="AdvertisedTimeAtLocation" value="{end_iso}" />
          </AND>
        </FILTER>
        <INCLUDE>AdvertisedTrainIdent</INCLUDE>
        <INCLUDE>LocationSignature</INCLUDE>
        <INCLUDE>ActivityType</INCLUDE>
        <INCLUDE>AdvertisedTimeAtLocation</INCLUDE>
        <INCLUDE>EstimatedTimeAtLocation</INCLUDE>
        <INCLUDE>TimeAtLocation</INCLUDE>
        <INCLUDE>Canceled</INCLUDE>
        <INCLUDE>Operator</INCLUDE>
      </QUERY>
    </REQUEST>
    """
    j = tv_post(xml)
    return j.get('RESPONSE', {}).get('RESULT', [{}])[0].get('TrainAnnouncement', []) or []

# --------------------- ETL logic ---------------------
def upsert_journey_and_stop_rows(anns):
    grouped = {}
    for a in anns:
        train_id = a.get('AdvertisedTrainIdent')
        adv = parse_iso_like(a.get('AdvertisedTimeAtLocation'))
        if not train_id or not adv:
            continue
        journey_date = adv.date().isoformat()
        grouped.setdefault((train_id, journey_date), []).append(a)

    db = get_db()
    c = db.cursor()
    total_stops_upd = 0
    total_journeys_new = 0

    for (train_id, jdate), rows in grouped.items():
        ops = [r.get('Operator') for r in rows if r.get('Operator')]
        operator = ops[0] if ops else None

        c.execute(
            "INSERT OR IGNORE INTO journeys(train_id, journey_date, operator) VALUES (?, ?, ?)",
            (train_id, jdate, operator)
        )
        if c.rowcount > 0:
            total_journeys_new += 1

        jid = c.execute(
            "SELECT journey_id FROM journeys WHERE train_id = ? AND journey_date = ?",
            (train_id, jdate)
        ).fetchone()
        if not jid:
            continue
        journey_id = jid['journey_id']

        stops = {}
        for r in rows:
            loc = r.get('LocationSignature')
            if not loc:
                continue
            st = stops.setdefault(loc, {
                "scheduled_arrival": None, "actual_arrival": None,
                "scheduled_departure": None, "actual_departure": None,
                "delay_minutes": None, "is_canceled": False
            })
            act = r.get('ActivityType')
            adv = parse_iso_like(r.get('AdvertisedTimeAtLocation'))
            eta = parse_iso_like(r.get('EstimatedTimeAtLocation'))
            tat = parse_iso_like(r.get('TimeAtLocation'))
            canceled = bool(r.get('Canceled', False))
            if act == 'Ankomst':
                if adv and (st["scheduled_arrival"] is None or adv < parse_iso_like(st["scheduled_arrival"])):
                    st["scheduled_arrival"] = to_iso_z(adv)
                actual = tat or eta
                if actual:
                    st["actual_arrival"] = to_iso_z(actual)
                    diff = round((actual - adv).total_seconds() / 60.0) if adv else None
                    st["delay_minutes"] = int(diff) if diff is not None else st["delay_minutes"]
            elif act == 'Avgang':
                if adv and (st["scheduled_departure"] is None or adv < parse_iso_like(st["scheduled_departure"])):
                    st["scheduled_departure"] = to_iso_z(adv)
                actual = tat or eta
                if actual:
                    st["actual_departure"] = to_iso_z(actual)
            st["is_canceled"] = st["is_canceled"] or canceled

        def stop_sort_key(item):
            _, rec = item
            candidates = [parse_iso_like(rec["scheduled_arrival"]), parse_iso_like(rec["scheduled_departure"])]
            candidates = [x for x in candidates if x is not None]
            return min(candidates) if candidates else datetime.max.replace(tzinfo=timezone.utc)


        ordered = sorted(stops.items(), key=stop_sort_key)

        seq = 0
        for station_sig, rec in ordered:
            seq += 1
            c.execute("""
                INSERT OR IGNORE INTO stops(
                    journey_id, sequence_number, station_signature,
                    scheduled_arrival, actual_arrival,
                    scheduled_departure, actual_departure,
                    delay_minutes, is_canceled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                journey_id, seq, station_sig,
                rec["scheduled_arrival"], rec["actual_arrival"],
                rec["scheduled_departure"], rec["actual_departure"],
                rec["delay_minutes"], int(rec["is_canceled"])
            ))
            if c.rowcount == 0:
                c.execute("""
                    UPDATE stops SET
                        scheduled_arrival = COALESCE(?, scheduled_arrival),
                        actual_arrival = COALESCE(?, actual_arrival),
                        scheduled_departure = COALESCE(?, scheduled_departure),
                        actual_departure = COALESCE(?, actual_departure),
                        delay_minutes = COALESCE(?, delay_minutes),
                        is_canceled = ?
                    WHERE journey_id = ? AND station_signature = ? AND sequence_number = ?
                """, (
                    rec["scheduled_arrival"], rec["actual_arrival"],
                    rec["scheduled_departure"], rec["actual_departure"],
                    rec["delay_minutes"], int(rec["is_canceled"]),
                    journey_id, station_sig, seq
                ))
            total_stops_upd += 1

    db.commit()
    db.close()
    return total_journeys_new, total_stops_upd

def rebuild_delays_regression():
    db = get_db()
    c = db.cursor()
    c.execute("DELETE FROM delays_regression;")
    rows = c.execute("""
        SELECT
            j.journey_id, j.train_id, j.journey_date,
            s.station_signature, st.station_name, st.lon, st.lat,
            s.scheduled_arrival AS advertised_time,
            s.delay_minutes,
            s.is_canceled
        FROM stops s
        JOIN journeys j ON j.journey_id = s.journey_id
        LEFT JOIN stations st ON st.station_signature = s.station_signature
        WHERE s.scheduled_arrival IS NOT NULL
        ORDER BY j.train_id, j.journey_date, s.scheduled_arrival
    """).fetchall()

    last_delay_by_journey = {}
    to_insert = []
    for r in rows:
        jid = r["journey_id"]
        adv = parse_iso_like(r["advertised_time"])
        if not adv:
            continue
        hour = adv.astimezone(timezone.utc).hour
        weekday = adv.astimezone(timezone.utc).weekday()
        prev_delay = last_delay_by_journey.get(jid)
        to_insert.append((
            jid, r["train_id"], r["journey_date"], r["station_signature"], r["station_name"],
            r["lon"], r["lat"], r["advertised_time"], "Ankomst",
            r["delay_minutes"], int(r["is_canceled"]), hour, weekday, prev_delay
        ))
        last_delay_by_journey[jid] = r["delay_minutes"]

    c.executemany("""
        INSERT INTO delays_regression(
            journey_id, train_id, journey_date,
            station_signature, station_name, lon, lat,
            advertised_time, activity_type,
            delay_minutes, is_canceled,
            hour, weekday, prev_delay_minutes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, to_insert)

    db.commit()
    db.close()
    print(f"[Regression] Wrote {len(to_insert)} rows into delays_regression")

# --------------------- Orchestration ---------------------
def main():
    ensure_schema()
    print(f"[Init] DB: {DATABASE}")
    print("[Init] Fetching stations …")
    fetch_stations()

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=LOOKBACK_HOURS)

    windows = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(hours=SLICE_HOURS), end)
        windows.append((cur, nxt))
        cur = nxt

    total_j = 0
    total_s = 0
    print(f"[Backfill] {len(windows)} slices covering last {LOOKBACK_HOURS}h (slice={SLICE_HOURS}h)")

    for ws, we in windows:
        print(f"[Window] {to_iso_z(ws)} → {to_iso_z(we)}")
        anns = fetch_announcements_window(ws, we)
        if not anns:
            print("  (no announcements)")
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue
        j_new, s_upd = upsert_journey_and_stop_rows(anns)
        total_j += j_new
        total_s += s_upd
        print(f"  +journeys: {j_new}, +stop rows touched: {s_upd}")
        time.sleep(SLEEP_BETWEEN_CALLS)

    print("[Build] Creating regression table …")
    rebuild_delays_regression()

    print(f"[Done] New journeys: {total_j}, stop rows touched: {total_s}")
    print(f"[DB] {DATABASE}")

if __name__ == "__main__":
    main()
