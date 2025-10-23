import { useEffect, useState, useRef } from "react";

// Helper function to format the delay status for display
function formatDelay(delay, isCanceled) {
  if (isCanceled) {
    return <span className="badge bg-dark">Canceled</span>;
  }
  if (delay === null || delay === undefined) {
    return "N/A";
  }
  if (delay > 0) {
    return <span className="badge bg-danger">{delay} min late</span>;
  }
  if (delay < 0) {
    return <span className="badge bg-info">{Math.abs(delay)} min early</span>;
  }
  return <span className="badge bg-success">On Time</span>;
}

// Helper to determine row styling
function getRowClass(delay, isCanceled) {
  if (isCanceled) return "table-dark";
  if (delay > 0) return "table-warning";
  return "";
}

// Normalize any backend payload shape into your row shape
function normalize(d) {
  const train_id = d.train_id ?? d.trainId ?? "?";
  const timestamp = d.timestamp ?? d.timeStamp ?? null;
  const delay_minutes = d.delay_minutes ?? d.delayMinutes ?? null;
  const is_canceled = d.is_canceled ?? d.isCanceled ?? false;
  const speed = d.speed ?? null;
  const lon = d.lon ?? d.longitude ?? null;
  const lat = d.lat ?? d.latitude ?? null;
  const nextStopSignature =
    d.nextStopSignature ?? d.next_stop_signature ?? null;
  const predictedDelayMinutes =
    d.predictedDelayMinutes ?? d.predicted_delay_minutes ?? null;
  const predictedAt = d.predictedAt ?? d.predicted_at ?? null;

  // Stable-ish key: train + timestamp (falls back to random if missing)
  const activity_id =
    train_id && timestamp
      ? `${train_id}-${timestamp}`
      : `${train_id}-${Math.random().toString(36).slice(2)}`;

  return {
    activity_id,
    train_id,
    delay_minutes,
    is_canceled,
    timestamp,
    speed,
    lon,
    lat,
    nextStopSignature,
    predictedDelayMinutes,
    predictedAt,
  };
}

export default function TrainTest() {
  const [rows, setRows] = useState([]);
  const [isConnected, setIsConnected] = useState(false);
  const eventSourceRef = useRef(null);
  const seenIdsRef = useRef(new Set()); // dedupe

  useEffect(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    // Prefer matching host/scheme to avoid mixed-content/CORS issues during dev
    const sseUrl = "http://localhost:5000/positions"; // or same-origin "/positions" if proxied
    const es = new EventSource(sseUrl, { withCredentials: false });
    eventSourceRef.current = es;

    es.onopen = () => setIsConnected(true);

    es.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);

        // Ignore heartbeats: { type: "heartbeat", ts: "..." }
        if (payload && payload.type === "heartbeat") return;

        const items = Array.isArray(payload) ? payload : [payload];
        const normalized = [];

        for (const item of items) {
          const r = normalize(item);
          if (!seenIdsRef.current.has(r.activity_id)) {
            seenIdsRef.current.add(r.activity_id);
            normalized.push(r);
          }
        }

        if (normalized.length > 0) {
          setRows((prev) => {
            const next = [...normalized, ...prev];
            // keep only latest 500
            if (next.length > 500) next.length = 500;
            return next;
          });
        }
      } catch (err) {
        console.error("Failed to parse SSE data:", err);
      }
    };

    es.onerror = () => {
      setIsConnected(false);
      // Let EventSource auto-reconnect; don’t close here or it won’t retry
    };

    return () => es.close();
  }, []);

  return (
    <div className="container py-3">
      <h3>Live Train Positions &amp; Status</h3>
      <p>
        Status:{" "}
        {isConnected ? (
          <span className="badge bg-success">Connected</span>
        ) : (
          <span className="badge bg-danger">Disconnected</span>
        )}
      </p>
      <p className="mb-2">
        Displaying latest <strong>{rows.length}</strong> train position updates.
      </p>

      <div
        className="table-responsive"
        style={{ maxHeight: "70vh", overflowY: "auto" }}
      >
        <table className="table table-sm table-hover align-middle">
          <thead className="table-light" style={{ position: "sticky", top: 0 }}>
            <tr>
              <th>Train</th>
              <th>Status</th>
              <th>Timestamp</th>
              <th>Speed (km/h)</th>
              <th>Longitude</th>
              <th>Latitude</th>
              <th>Delayed</th>
              <th>Next Stop</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.activity_id}
                className={getRowClass(r.delay_minutes, r.is_canceled)}
              >
                <td>
                  <strong>{r.train_id ?? "?"}</strong>
                </td>
                <td>{formatDelay(r.delay_minutes, r.is_canceled)}</td>
                <td>
                  {r.timestamp ? new Date(r.timestamp).toLocaleString() : "—"}
                </td>
                <td>
                  {typeof r.speed === "number" ? Math.round(r.speed) : "—"}
                </td>
                <td>{typeof r.lon === "number" ? r.lon.toFixed(4) : "—"}</td>
                <td>{typeof r.lat === "number" ? r.lat.toFixed(4) : "—"}</td>
                <td>
                  {typeof r.predictedDelayMinutes === "number"
                    ? r.predictedDelayMinutes.toFixed(1) + " minute(s) delayed"
                    : "-"}
                </td>
                <td>{r.nextStopSignature}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
