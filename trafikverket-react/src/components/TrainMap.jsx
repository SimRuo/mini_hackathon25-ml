import { useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Tooltip, LayersControl, LayerGroup, ScaleControl } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet-timedimension/dist/leaflet.timedimension.control.css";

/* ---- Styling helpers ---- */
function colorByDelay(delay) {
  if (delay == null) return "#4e6cff"; // unknown → indigo
  if (delay < 0) return "#17a2b8"; // early → cyan
  if (delay === 0) return "#28a745"; // on time → green
  if (delay <= 5) return "#fd7e14"; // ≤5 late → orange
  return "#dc3545"; // >5 late → red
}
function radiusBySpeed(speed) {
  const s = typeof speed === "number" ? speed : 0;
  // return Math.min(14, Math.max(5, 5 + s / 30)); // 0→5px, 270km/h→≈14px
  return 7;
}
function fmtPredicted(delay) {
  if (delay == null) return "—";
  if (delay === 0) return "On time";
  if (delay < 0) return `${Math.abs(delay).toFixed(1)} min early`;
  return `${delay.toFixed(1)} min late`;
}

/* ---- Legend ---- */
function MapLegend() {
  const items = [
    { color: "#17a2b8", label: "Early" },
    { color: "#28a745", label: "On time" },
    { color: "#fd7e14", label: "≤ 5 min late" },
    { color: "#dc3545", label: "> 5 min late" },
    { color: "#4e6cff", label: "Unknown" },
    { color: "#6c757d", label: "Station" },
  ];
  return (
    <div
      className="position-absolute"
      style={{
        right: 12,
        bottom: 12,
        zIndex: 1000,
        background: "rgba(255,255,255,0.92)",
        borderRadius: 12,
        boxShadow: "0 6px 20px rgba(0,0,0,0.15)",
        padding: "10px 12px",
      }}
    >
      <div className="fw-semibold mb-2" style={{ fontSize: 13 }}>
        Status
      </div>
      {items.map((it) => (
        <div key={it.label} className="d-flex align-items-center gap-2" style={{ fontSize: 12, lineHeight: 1.2, marginBottom: 6 }}>
          <span
            style={{
              display: "inline-block",
              width: 12,
              height: 12,
              borderRadius: 999,
              background: it.color,
            }}
          />
          <span>{it.label}</span>
        </div>
      ))}
    </div>
  );
}

/* ---- Main component ---- */
export default function TrainMap() {
  const [trains, setTrains] = useState(new Map()); // key: trainId -> {...}
  const [stations, setStations] = useState([]);
  const [connected, setConnected] = useState(false);
  const seenTsRef = useRef(new Map()); // trainId -> last timestamp (ms)

  // --- Time-travel state ---
  const historyRef = useRef([]);
  const lastSnapTsRef = useRef(0);
  const [selectedIndex, setSelectedIndex] = useState(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const MAX_FRAMES = 720;
  const SNAPSHOT_EVERY_MS = 10_000;

  // Load stations once
  useEffect(() => {
    fetch("/api/stations")
      .then((r) => r.json())
      .then(({ stations }) => setStations(stations ?? []))
      .catch(() => setStations([]));
  }, []);

  // Playback
  useEffect(() => {
    if (!isPlaying) return;
    const id = setInterval(() => {
      const frames = historyRef.current;
      if (frames.length === 0) return;
      if (selectedIndex === null) {
        setSelectedIndex(Math.max(0, frames.length - 2));
        return;
      }
      setSelectedIndex((idx) => Math.min(idx + 1, frames.length - 1));
    }, 700);
    return () => clearInterval(id);
  }, [isPlaying, selectedIndex]);

  // SSE stream
  useEffect(() => {
    const es = new EventSource("/positions");
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (evt) => {
      try {
        const payload = JSON.parse(evt.data);
        if (payload?.type === "heartbeat") return;

        const items = Array.isArray(payload) ? payload : [payload];
        setTrains((prev) => {
          const next = new Map(prev);
          for (const p of items) {
            const trainId = p.train_id ?? p.trainId;
            const lat = p.lat ?? p.latitude;
            const lon = p.lon ?? p.longitude;
            const speed = p.speed ?? null;
            const delay = p.delay_minutes ?? p.delayMinutes ?? null;
            const isCanceled = p.is_canceled ?? p.isCanceled ?? false;
            const tsStr = p.timestamp ?? p.timeStamp ?? null;
            const ts = tsStr ? Date.parse(tsStr) : Date.now();

            // NEW: predicted fields (from backend)
            const nextStopSignature = p.nextStopSignature ?? p.next_stop_signature ?? null;
            const predictedDelayMinutes = p.predictedDelayMinutes ?? p.predicted_delay_minutes ?? null;
            const predictedAt = p.predictedAt ?? p.predicted_at ?? null;

            if (!trainId || lat == null || lon == null) continue;

            const last = seenTsRef.current.get(trainId) ?? 0;
            if (ts < last) continue; // drop stale
            seenTsRef.current.set(trainId, ts);

            next.set(trainId, {
              lat,
              lon,
              speed,
              delayMinutes: delay,
              canceled: isCanceled,
              ts,
              // keep the predicted data on the marker
              nextStopSignature,
              predictedDelayMinutes,
              predictedAt,
            });
          }
          // Snapshot buffering
          const now = Date.now();
          if (now - lastSnapTsRef.current >= SNAPSHOT_EVERY_MS) {
            lastSnapTsRef.current = now;
            const entries = Array.from(next.entries());
            const frames = historyRef.current;
            frames.push({ ts: now, entries });
            if (frames.length > MAX_FRAMES) frames.splice(0, frames.length - MAX_FRAMES);
            if (isPlaying && selectedIndex !== null) {
              const nextIdx = Math.min(selectedIndex + 1, frames.length - 1);
              setSelectedIndex(nextIdx);
            }
          }
          return next;
        });
      } catch {
        /* ignore */
      }
    };
    return () => es.close();
  }, []);

  const liveFeatures = useMemo(() => Array.from(trains.entries()), [trains]);
  const frames = historyRef.current;
  const isLive = selectedIndex === null || frames.length === 0;
  const trainFeatures = isLive ? liveFeatures : frames[selectedIndex]?.entries ?? [];

  return (
    <div className="container-fluid px-3 py-3">
      <div className="d-flex align-items-center justify-content-between mb-2">
        <h4 className="mb-0">Live Trains</h4>
        <span className={`badge ${connected ? "bg-success" : "bg-danger"}`}>{connected ? "Live" : "Reconnecting…"}</span>
      </div>

      {/* Time-travel controls */}
      <div className="d-flex align-items-center gap-2 mb-2">
        <button
          className="btn btn-sm btn-outline-secondary"
          onClick={() => {
            setSelectedIndex(null);
            setIsPlaying(false);
          }}
          disabled={historyRef.current.length === 0 && selectedIndex === null}
          title="Visa live"
        >
          Live
        </button>
        <button
          className="btn btn-sm btn-outline-primary"
          onClick={() => setIsPlaying((p) => !p)}
          disabled={historyRef.current.length < 2}
          title="Spela/Pausa historik"
        >
          {isPlaying ? "Pausa" : "Spela"}
        </button>
        <input
          type="range"
          style={{ width: 300 }}
          min={0}
          max={Math.max(0, historyRef.current.length - 1)}
          step={1}
          value={selectedIndex === null ? Math.max(0, historyRef.current.length - 1) : selectedIndex}
          onChange={(e) => {
            const idx = Number(e.target.value);
            setSelectedIndex(idx);
            setIsPlaying(false);
          }}
        />
        <code style={{ fontSize: 12 }}>
          {(() => {
            const frames = historyRef.current;
            const idx = selectedIndex ?? frames.length - 1;
            const ts = frames[idx]?.ts ?? Date.now();
            return new Date(ts).toLocaleTimeString();
          })()}
        </code>
        {!isLive && <span className="badge bg-secondary">Historik</span>}
      </div>

      <div className="rounded-4 overflow-hidden shadow" style={{ height: "85vh", border: "1px solid #e9ecef" }}>
        <MapContainer
          center={[59.33, 18.06]}
          zoom={6}
          minZoom={4}
          maxZoom={17}
          style={{ height: "100%", width: "100%" }}
          worldCopyJump={true}
        >
          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="&copy; OpenStreetMap" />

          <LayersControl position="topright">
            <LayersControl.Overlay checked name="Trains">
              <LayerGroup>
                {trainFeatures.map(([id, t]) => (
                  <CircleMarker
                    key={id}
                    center={[t.lat, t.lon]}
                    radius={radiusBySpeed(t.speed)}
                    pathOptions={{
                      color: t.canceled ? "#6c757d" : colorByDelay(t.delayMinutes),
                      fillColor: t.canceled ? "#6c757d" : colorByDelay(t.delayMinutes),
                      fillOpacity: 0.85,
                      weight: 1.25,
                    }}
                  >
                    <Tooltip direction="top" offset={[0, -6]} opacity={1} sticky>
                      <div style={{ fontSize: 12 }}>
                        <div className="fw-semibold">Train {id}</div>
                        <div>{new Date(t.ts).toLocaleTimeString()}</div>
                        <div>Speed: {t.speed != null ? Math.round(t.speed) : "—"} km/h</div>
                        <div>
                          Status:{" "}
                          {t.canceled
                            ? "Canceled"
                            : t.delayMinutes == null
                            ? "Unknown"
                            : t.delayMinutes === 0
                            ? "On time"
                            : t.delayMinutes < 0
                            ? `${Math.abs(t.delayMinutes)} min early`
                            : `${t.delayMinutes} min late`}
                        </div>

                        {/* --- NEW: Predicted delay for the next stop --- */}
                        <div className="mt-1">
                          <span className="text-muted">Next stop</span> <strong>{t.nextStopSignature ?? "—"}</strong>:{" "}
                          <em>{fmtPredicted(t.predictedDelayMinutes)}</em>
                          {t.predictedAt && (
                            <>
                              {" "}
                              <span className="text-muted">(as of {new Date(t.predictedAt).toLocaleTimeString()})</span>
                            </>
                          )}
                        </div>
                      </div>
                    </Tooltip>
                  </CircleMarker>
                ))}
              </LayerGroup>
            </LayersControl.Overlay>

            <LayersControl.Overlay name="Stations">
              <LayerGroup>
                {stations.map((s) => (
                  <CircleMarker
                    key={s.station_signature}
                    center={[s.lat, s.lon]}
                    radius={4}
                    pathOptions={{
                      color: "#6c757d",
                      fillColor: "#6c757d",
                      fillOpacity: 0.9,
                      weight: 1,
                    }}
                  >
                    <Tooltip direction="top" offset={[0, -6]} opacity={1} sticky>
                      <div style={{ fontSize: 12 }}>
                        <div className="fw-semibold">{s.station_name}</div>
                        <div className="text-muted">{s.station_signature}</div>
                      </div>
                    </Tooltip>
                  </CircleMarker>
                ))}
              </LayerGroup>
            </LayersControl.Overlay>
          </LayersControl>

          <ScaleControl position="bottomleft" />
          <MapLegend />
        </MapContainer>
      </div>
    </div>
  );
}
