import sqlite3
import requests
import json
import os
import time
import threading
import queue
import math
from flask import Flask, Response, cli
from flask_cors import CORS
from datetime import datetime, timezone, timedelta
from flask.cli import with_appcontext
from joblib import load
import pandas as pd

# --- Configuration ---
DATABASE = 'train_data.db'
API_KEY = "demokey"
TV_API_URL = "https://api.trafikinfo.trafikverket.se/v2/data.json"
ANNOUNCEMENT_POLL_INTERVAL_SECONDS = 120 # Fetch schedule updates every 2 minutes
DELAY_LOOKBACK_MINUTES = int(os.environ.get("DELAY_LOOKBACK_MINUTES", "120"))  # default 2h

MODEL_PATH = "artifacts/delay_pipeline.joblib" 
MODEL_VERSION = "1.0"

try:
    model = load(MODEL_PATH)
    print(f"[MODEL] Loaded delay model from {MODEL_PATH}")
except Exception as e:
    print(f"[MODEL ERROR] Could not load model: {e}")
    model = None

broadcast_queue = queue.Queue()
listeners = []

# --- Shared State ---
# These are shared between threads, so access must be thread-safe.
delay_cache = {}
delay_cache_lock = threading.Lock()
broadcast_queue = queue.Queue()
new_train_queue = queue.Queue()

# --- Database & Helpers ---
def get_db_conn():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db_conn()
    with open('schema.sql', 'r') as f:
        db.cursor().executescript(f.read())
    db.commit()
    db.close()
    print("Initialized the database with the new schema.")

def parse_iso_time(time_str):
    if not time_str: return None
    try:
        # Handle different precisions of fractional seconds
        if '.' in time_str:
            time_str = time_str.split('.')[0]
        return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def _hhmmss_from_minutes(mins: int) -> str:
    mins = max(1, int(mins))
    h, m = divmod(mins, 60)
    return f"{h}:{m:02d}:00"

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371  # Radius of Earth in kilometers
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat / 2) * math.sin(dLat / 2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dLon / 2) * math.sin(dLon / 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# --- WORKER 1: Fetch Station Data (runs once at startup) ---
def station_worker():
    print("[STATION WORKER] Fetching all station data...")
    try:
        query = f"""
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
        headers = {'Content-Type': 'text/xml'}
        response = requests.post(TV_API_URL, data=query, headers=headers, timeout=60)
        response.raise_for_status()
        stations = response.json()['RESPONSE']['RESULT'][0]['TrainStation']

        db = get_db_conn()
        cursor = db.cursor()
        for station in stations:
            wkt = station.get('Geometry', {}).get('WGS84')
            lon, lat = None, None
            if wkt and 'POINT' in wkt:
                parts = wkt.replace('POINT (', '').replace(')', '').split()
                if len(parts) == 2: lon, lat = float(parts[0]), float(parts[1])
            
            cursor.execute(
                "INSERT OR REPLACE INTO stations (station_signature, station_name, lon, lat) VALUES (?, ?, ?, ?)",
                (station['LocationSignature'], station['AdvertisedLocationName'], lon, lat)
            )
        db.commit()
        db.close()
        print(f"[STATION WORKER] Successfully stored data for {len(stations)} stations.")
    except Exception as e:
        print(f"[STATION WORKER ERROR] {e}")


# --- WORKER 2: Fetch Full Journey Schedules for New Trains ---
def journey_worker():
    print("[JOURNEY WORKER] Starting...")
    known_journeys = set()
    db = get_db_conn()
    for row in db.execute("SELECT train_id, journey_date FROM journeys"):
        known_journeys.add((row['train_id'], row['journey_date']))
    db.close()

    while True:
        train_id, journey_date_str = new_train_queue.get()
        if (train_id, journey_date_str) in known_journeys:
            continue

        print(f"[JOURNEY WORKER] New train discovered: {train_id} on {journey_date_str}. Fetching schedule...")
        try:
            # Calculate a 24-hour window for the journey date
            start_date = datetime.fromisoformat(journey_date_str)
            end_date = start_date + timedelta(days=1)
            start_date_iso = start_date.isoformat() + "Z"
            end_date_iso = end_date.isoformat() + "Z"

            query = f"""
            <REQUEST>
                <LOGIN authenticationkey="{API_KEY}" />
                <QUERY objecttype="TrainAnnouncement" schemaversion="1.6" orderby="AdvertisedTimeAtLocation">
                    <FILTER>
                        <AND>
                            <EQ name="AdvertisedTrainIdent" value="{train_id}" />
                            <EQ name="Advertised" value="true" />
                            <GTE name="AdvertisedTimeAtLocation" value="{start_date_iso}" />
                            <LT name="AdvertisedTimeAtLocation" value="{end_date_iso}" />
                        </AND>
                    </FILTER>
                    <INCLUDE>LocationSignature</INCLUDE>
                    <INCLUDE>ActivityType</INCLUDE>
                    <INCLUDE>AdvertisedTimeAtLocation</INCLUDE>
                    <INCLUDE>Operator</INCLUDE>
                </QUERY>
            </REQUEST>
            """

            headers = {'Content-Type': 'text/xml'}
            response = requests.post(TV_API_URL, data=query, headers=headers, timeout=30)
            response.raise_for_status()
            
            announcements = response.json().get('RESPONSE',{}).get('RESULT',[{}])[0].get('TrainAnnouncement',[])
            if not announcements: 
                print(f"[JOURNEY WORKER] No schedule found for {train_id} on {journey_date_str}")
                continue

            operator = announcements[0].get('Operator')
            
            db = get_db_conn()
            cursor = db.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO journeys (train_id, journey_date, operator) VALUES (?, ?, ?)",
                (train_id, journey_date_str, operator)
            )
            journey_id_row = cursor.execute("SELECT journey_id FROM journeys WHERE train_id = ? AND journey_date = ?", (train_id, journey_date_str)).fetchone()
            if not journey_id_row: continue
            journey_id = journey_id_row['journey_id']

            stops_data = {}
            for ann in announcements:
                loc_sig = ann['LocationSignature']
                if loc_sig not in stops_data: stops_data[loc_sig] = {}
                activity_type = ann['ActivityType'].lower()
                stops_data[loc_sig][f"scheduled_{activity_type}"] = parse_iso_time(ann['AdvertisedTimeAtLocation'])
            
            seq = 0
            for loc_sig, times in stops_data.items():
                seq += 1
                cursor.execute(
                    """INSERT OR IGNORE INTO stops (journey_id, sequence_number, station_signature, scheduled_arrival, scheduled_departure) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (journey_id, seq, loc_sig, times.get('scheduled_ankomst'), times.get('scheduled_avgang'))
                )
            
            db.commit()
            db.close()
            known_journeys.add((train_id, journey_date_str))
            print(f"[JOURNEY WORKER] Stored schedule with {len(stops_data)} stops for {train_id}.")
        except Exception as e:
            print(f"[JOURNEY WORKER ERROR] for {train_id}: {e}")


# --- WORKER 3: Process Live Positions & Create Explanatory Variables ---
def position_worker():
    print("[POSITION WORKER] Starting...")
    station_locations = {}
    db = get_db_conn()
    for row in db.execute("SELECT station_signature, lon, lat FROM stations WHERE lon IS NOT NULL AND lat IS NOT NULL"):
        station_locations[row['station_signature']] = (row['lon'], row['lat'])
    db.close()
    
    while True:
        try:
            xml_query = f"""
            <REQUEST>
              <LOGIN authenticationkey="{API_KEY}" />
              <QUERY objecttype="TrainPosition" namespace="järnväg.trafikinfo" schemaversion="1.1" sseurl="true">
                <FILTER><EQ name="Status.Active" value="true" /></FILTER>
                <INCLUDE>Train.AdvertisedTrainNumber</INCLUDE>
                <INCLUDE>Position.WGS84</INCLUDE>
                <INCLUDE>TimeStamp</INCLUDE>
                <INCLUDE>Speed</INCLUDE>
              </QUERY>
            </REQUEST>
            """
            headers = {'Content-Type': 'text/xml'}
            response = requests.post(TV_API_URL, data=xml_query, headers=headers, timeout=20)
            response.raise_for_status()
            sse_url = response.json()['RESPONSE']['RESULT'][0]['INFO']['SSEURL']
            
            with requests.get(sse_url, stream=True, headers={'Accept': 'text/event-stream'}, timeout=None) as client:
                print("[POSITION WORKER] Connected to SSE stream.")
                for line in client.iter_lines():
                    if not line.startswith(b'data:'): continue
                    
                    data_str = line.decode('utf-8')[5:]
                    payload = json.loads(data_str)
                    positions = payload.get('RESPONSE', {}).get('RESULT', [{}])[0].get('TrainPosition', [])

                    db = get_db_conn()
                    cursor = db.cursor()
                    
                    for pos in positions:
                        train_id = pos.get('Train', {}).get('AdvertisedTrainNumber')
                        timestamp_str = pos.get('TimeStamp')
                        if not train_id or not timestamp_str: continue

                        op_date = timestamp_str[:10]
                        new_train_queue.put((train_id, op_date))

                        journey_row = cursor.execute("SELECT journey_id FROM journeys WHERE train_id = ? AND journey_date = ?", (train_id, op_date)).fetchone()
                        if not journey_row: continue
                        journey_id = journey_row['journey_id']

                        wkt = pos.get('Position', {}).get('WGS84')
                        lon, lat = None, None
                        if wkt and 'POINT' in wkt:
                            parts = wkt.replace('POINT (', '').replace(')', '').split()
                            if len(parts) == 2: lon, lat = float(parts[0]), float(parts[1])
                        
                        # --- FEATURE CREATION: distance_to_next_stop_km ---
                        distance_to_next = None
                        now_time = parse_iso_time(timestamp_str)
                        
                        next_stop_row = cursor.execute(
                            """SELECT s.station_signature FROM stops s
                               WHERE s.journey_id = ? AND (s.scheduled_arrival > ? OR s.scheduled_departure > ?)
                               ORDER BY s.sequence_number ASC LIMIT 1""", (journey_id, now_time, now_time)
                        ).fetchone()

                        if next_stop_row and lon and lat:
                            next_stop_sig = next_stop_row['station_signature']
                            next_stop_lon, next_stop_lat = station_locations.get(next_stop_sig, (None, None))
                            if next_stop_lon and next_stop_lat:
                                distance_to_next = haversine_distance(lat, lon, next_stop_lat, next_stop_lon)

                        predicted_delay = None
                        predicted_at = None
                        if next_stop_sig:
                            pred_row = cursor.execute(
                                """SELECT predicted_delay_minutes, prediction_time
                                FROM predicted_delays
                                WHERE journey_id = ? AND station_signature = ?
                                ORDER BY datetime(prediction_time) DESC
                                LIMIT 1""",
                                (journey_id, next_stop_sig)
                            ).fetchone()
                            if pred_row:
                                predicted_delay = pred_row['predicted_delay_minutes']
                                predicted_at = pred_row['prediction_time']

                        delay_minutes = None
                        try:
                            delay_row = cursor.execute("""
                                SELECT delay_minutes
                                FROM stops
                                WHERE journey_id = ?
                                AND delay_minutes IS NOT NULL
                                ORDER BY ABS(
                                    julianday(scheduled_arrival) - julianday(?)
                                ) ASC
                                LIMIT 1
                            """, (journey_id, now_time)).fetchone()

                            if delay_row:
                                delay_minutes = delay_row['delay_minutes']

                        except Exception:
                            pass
                        log_data = (journey_id, now_time, pos.get('Speed'), lon, lat, distance_to_next)
                        if lon is not None and lat is not None:
                            msg = {
                                "trainId": str(train_id),
                                "journeyDate": op_date,
                                "timestamp": timestamp_str,
                                "speed": pos.get('Speed'),
                                "lon": lon,
                                "lat": lat,
                                "distanceToNextKm": distance_to_next,
                                "delayMinutes": delay_minutes,              
                                "nextStopSignature": next_stop_sig,         
                                "predictedDelayMinutes": predicted_delay,    
                                "predictedAt": predicted_at,                
                            }

                            broadcast_queue.put(msg)
                        cursor.execute(
                            "INSERT INTO position_logs (journey_id, timestamp, speed, lon, lat, distance_to_next_stop_km) VALUES (?, ?, ?, ?, ?, ?)",
                            log_data
                        )                    
                    db.commit()
                    db.close()
        except Exception as e:
            print(f"[POSITION WORKER ERROR] {e}. Retrying in 30s...")
            time.sleep(30)

# --- WORKER 4: Update Delays for Completed Stops  ---
def delay_update_worker():
    print("[DELAY WORKER] Starting...")
    while True:
        try:
            lookback = _hhmmss_from_minutes(DELAY_LOOKBACK_MINUTES)

            query = f"""
            <REQUEST>
              <LOGIN authenticationkey="{API_KEY}" />
              <QUERY objecttype="TrainAnnouncement" schemaversion="1.6">
                <FILTER>
                  <AND>
                    <EQ name="Advertised" value="true" />
                    <OR>
                      <EQ name="ActivityType" value="Ankomst" />
                      <EQ name="ActivityType" value="Avgang" />
                    </OR>
                    <OR>
                      <AND>
                        <GT name="TimeAtLocation" value="$dateadd(-{lookback})" />
                        <LT name="TimeAtLocation" value="$now" />
                      </AND>
                      <AND>
                        <GT name="EstimatedTimeAtLocation" value="$dateadd(-{lookback})" />
                        <LT name="EstimatedTimeAtLocation" value="$now" />
                      </AND>
                    </OR>
                  </AND>
                </FILTER>
                <INCLUDE>AdvertisedTrainIdent</INCLUDE>
                <INCLUDE>LocationSignature</INCLUDE>
                <INCLUDE>ActivityType</INCLUDE>
                <INCLUDE>AdvertisedTimeAtLocation</INCLUDE>
                <INCLUDE>EstimatedTimeAtLocation</INCLUDE>
                <INCLUDE>TimeAtLocation</INCLUDE>
                <INCLUDE>Canceled</INCLUDE>
              </QUERY>
            </REQUEST>
            """
            headers = {'Content-Type': 'text/xml'}
            response = requests.post(TV_API_URL, data=query, headers=headers, timeout=30)
            response.raise_for_status()

            anns = response.json().get('RESPONSE', {}).get('RESULT', [{}])[0].get('TrainAnnouncement', [])
            if not anns:
                time.sleep(60)
                continue

            db = get_db_conn()
            cursor = db.cursor()
            arrivals, departures = 0, 0

            for ann in anns:
                train_id = ann.get('AdvertisedTrainIdent')
                loc_sig = ann.get('LocationSignature')
                act = ann.get('ActivityType')           # 'Ankomst' / 'Avgang'
                adv = parse_iso_time(ann.get('AdvertisedTimeAtLocation'))
                eta = parse_iso_time(ann.get('EstimatedTimeAtLocation'))
                t_at = parse_iso_time(ann.get('TimeAtLocation'))
                canceled = ann.get('Canceled', False)

                if not (train_id and loc_sig and adv and act):
                    continue

                actual_time = t_at or eta
                if not actual_time:
                    continue  # nothing to compute

                # Find journey by the operational date
                journey_date_str = adv.strftime('%Y-%m-%d')
                row = cursor.execute(
                    "SELECT journey_id FROM journeys WHERE train_id = ? AND journey_date = ?",
                    (train_id, journey_date_str)
                ).fetchone()
                if not row:
                    continue
                journey_id = row['journey_id']

                # Round to nearest minute to allow real zeros
                diff_minutes = round((actual_time - adv).total_seconds() / 60.0)

                if act == 'Ankomst':
                    cursor.execute(
                        """UPDATE stops
                        SET actual_arrival = ?, delay_minutes = ?, is_canceled = ?
                        WHERE journey_id = ? AND station_signature = ? AND actual_arrival IS NULL""",
                        (actual_time, int(diff_minutes), canceled, journey_id, loc_sig)
                    )
                    if cursor.rowcount > 0:
                        arrivals += 1

                        # ---------- PREDICT NEXT STOP DELAY  ----------
                        try:
                            # 1) find current sequence_number
                            cur_seq = cursor.execute(
                                """SELECT sequence_number FROM stops
                                WHERE journey_id = ? AND station_signature = ?
                                ORDER BY sequence_number LIMIT 1""",
                                (journey_id, loc_sig)
                            ).fetchone()

                            # 2) find the *next* stop with a scheduled arrival
                            if cur_seq:
                                next_row = cursor.execute(
                                    """SELECT s.station_signature, s.scheduled_arrival, st.lon, st.lat
                                    FROM stops s
                                    LEFT JOIN stations st ON st.station_signature = s.station_signature
                                    WHERE s.journey_id = ? AND s.sequence_number > ? AND s.scheduled_arrival IS NOT NULL
                                    ORDER BY s.sequence_number ASC
                                    LIMIT 1""",
                                    (journey_id, cur_seq['sequence_number'])
                                ).fetchone()

                                if next_row is None:
                                    print("[MODEL] fan va dåligt dedär gick")
                                    # anledningen till att vi får None här är för att alla tåg har inte scheduled_arrival
                                    # dom har bara scheduled_departure får fixa det sen
                                    # Tror att det är det här som gör att vi får massa tomma fält i predicted_delays

                                if next_row and model is not None:
                                
                                    adv_next_dt = parse_iso_time(next_row['scheduled_arrival']) or datetime.utcnow().replace(tzinfo=timezone.utc)

                                    features = {
                                        'hour': adv_next_dt.hour,
                                        'weekday': adv_next_dt.weekday(),
                                        'prev_delay_minutes': int(diff_minutes),   # propagation from this arrival
                                        'lon': float(next_row['lon'] or 0.0),
                                        'lat': float(next_row['lat'] or 0.0),
                                        'is_canceled': int(bool(canceled)),
                                        'station_signature': next_row['station_signature'],
                                    }

                                    try:
                                        yhat = float(model.predict(pd.DataFrame([features]))[0])
                                        cursor.execute(
                                            """INSERT INTO predicted_delays
                                            (journey_id, station_signature, predicted_delay_minutes, model_version, prediction_time)
                                            VALUES (?, ?, ?, ?, ?)""",
                                            (journey_id, next_row['station_signature'], yhat, MODEL_VERSION, datetime.utcnow().isoformat())
                                        )
                                        # Note: commit happens below when arrivals/departures > 0
                                        print(f"[MODEL] Predicted {yhat:.2f} min for next stop {next_row['station_signature']} (journey {journey_id})")
                                    except Exception as me:
                                        print(f"[MODEL] prediction failed: {me}")
                        except Exception as e_pred:
                            print(f"[MODEL] next-stop selection failed: {e_pred}")
                        # ---------- END PREDICT ----------

                else:  # 'Avgang'
                    cursor.execute(
                        """UPDATE stops
                           SET actual_departure = ?, is_canceled = ?
                           WHERE journey_id = ? AND station_signature = ? AND actual_departure IS NULL""",
                        (actual_time, canceled, journey_id, loc_sig)
                    )
                    if cursor.rowcount > 0: departures += 1

            if arrivals or departures:
                db.commit()
                print(f"[DELAY WORKER] Updated {arrivals} arrival(s), {departures} departure(s).")
            db.close()

        except Exception as e:
            print(f"[DELAY WORKER ERROR] {e}")

        time.sleep(60)



# --- Flask Application ---
app = Flask(__name__)
CORS(app)

@app.cli.command('init-db')
@with_appcontext
def init_db_command():
    init_db()

@app.route('/positions')
def get_positions():
    def event_stream():
        client_queue = queue.Queue()
        listeners.append(client_queue)
        print(f"Client connected. Total clients: {len(listeners)}")
        try:
            while True:
                data = client_queue.get()
                yield f"data: {json.dumps(data)}\n\n"
        except GeneratorExit:
            pass # Client disconnected
        finally:
            if client_queue in listeners:
                listeners.remove(client_queue)
            print(f"Client disconnected. Total clients: {len(listeners)}")
    return Response(event_stream(), content_type='text/event-stream')

@app.route('/api/stations')
def get_stations():
    db = get_db_conn()
    rows = db.execute("""
        SELECT station_signature, station_name, lon, lat
        FROM stations
        WHERE lon IS NOT NULL AND lat IS NOT NULL
    """).fetchall()
    db.close()
    return {"stations": [dict(r) for r in rows]}

def broadcast_data():
    while True:
        data = broadcast_queue.get()
        for client_queue in listeners[:]:
            try:
                client_queue.put(data)
            except Exception:
                # If putting fails, the client might be gone.
                # The 'finally' block in event_stream will handle removal.
                pass

def start_workers():
    print("--- Starting background data collection threads ---")
    
    # Run station worker and wait for it to finish so other workers have station data
    station_thread = threading.Thread(target=station_worker, daemon=True)
    station_thread.start()
    station_thread.join() # Wait for stations to be loaded

    # Start the other workers
    threading.Thread(target=journey_worker, daemon=True).start()
    threading.Thread(target=position_worker, daemon=True).start()
    threading.Thread(target=delay_update_worker, daemon=True).start()
    threading.Thread(target=broadcast_data, daemon=True).start()

    print("--- All background threads are running ---")


# 'flask run'
if __name__ != '__main__':
    start_workers()
