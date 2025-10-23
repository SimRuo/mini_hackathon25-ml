-- Deletes old tables to ensure a clean start.
DROP TABLE IF EXISTS positions;
DROP TABLE IF EXISTS position_logs;
DROP TABLE IF EXISTS stops;
DROP TABLE IF EXISTS journeys;
DROP TABLE IF EXISTS stations;

-- Table to store static information about each train station, including its coordinates.
CREATE TABLE stations (
  station_signature TEXT PRIMARY KEY,
  station_name TEXT NOT NULL,
  lon REAL,
  lat REAL
);

-- Table to define a unique journey for a specific train on a specific date.
CREATE TABLE journeys (
  journey_id INTEGER PRIMARY KEY AUTOINCREMENT,
  train_id TEXT NOT NULL,
  journey_date TEXT NOT NULL,
  operator TEXT,
  -- A journey is unique for a train on a given date.
  UNIQUE(train_id, journey_date)
);

-- Table to store the full, planned schedule for each journey.
CREATE TABLE stops (
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
  FOREIGN KEY (journey_id) REFERENCES journeys (journey_id),
  FOREIGN KEY (station_signature) REFERENCES stations (station_signature)
);

-- This table will now log every single position update and link it to a specific journey.
CREATE TABLE position_logs (
  log_id INTEGER PRIMARY KEY AUTOINCREMENT,
  journey_id INTEGER NOT NULL,
  timestamp TEXT NOT NULL,
  speed REAL,
  lon REAL,
  lat REAL,
  distance_to_next_stop_km REAL, -- This is one of our key predictive features!
  FOREIGN KEY (journey_id) REFERENCES journeys (journey_id)
);

CREATE TABLE predicted_delays (
  prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
  journey_id INTEGER NOT NULL,
  station_signature TEXT NOT NULL,
  predicted_delay_minutes REAL,
  model_version TEXT DEFAULT '1.0',
  prediction_time TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (journey_id) REFERENCES journeys (journey_id),
  FOREIGN KEY (station_signature) REFERENCES stations (station_signature)
);