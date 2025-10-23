import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { API_URL, tvXmlQuery, trainAnnouncementForStationXML } from "../tvQuery";
import "bootstrap/dist/css/bootstrap.min.css";

function toRow(n) {
  const toLocs = (n?.ToLocation || [])
    .flat()
    .map((x) => x.LocationName || x)
    .filter(Boolean);

  return {
    train: n?.AdvertisedTrainIdent || "",
    time: n?.AdvertisedTimeAtLocation ? new Date(n.AdvertisedTimeAtLocation).toLocaleString() : "",
    track: n?.TrackAtLocation || undefined,
    to: toLocs.length ? toLocs : undefined,
    owner: n?.InformationOwner || undefined,
  };
}

export default function Departures({ initialStation = "Cst", minutesAhead = 60, autoStart = false, onError }) {
  const [station, setStation] = useState(initialStation);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [sseUrl, setSseUrl] = useState(null);
  const esRef = useRef(null);

  const API_KEY = import.meta.env.VITE_TV_API_KEY;

  const xmlPayload = useMemo(() => {
    const body = trainAnnouncementForStationXML(station, minutesAhead, false);
    return tvXmlQuery(API_KEY, body);
  }, [station, minutesAhead, API_KEY]);

  async function fetchOnce() {
    cleanupSse();
    setLoading(true);
    setError(null);
    try {
      const res = await axios.post(API_URL, xmlPayload, {
        headers: { "Content-Type": "text/xml" },
        responseType: "json",
      });
      const list = res.data?.RESPONSE?.RESULT?.[0]?.TrainAnnouncement ?? [];
      setRows(list.map(toRow));
    } catch (e) {
      handleErr(e);
    } finally {
      setLoading(false);
    }
  }

  async function startStreaming() {
    cleanupSse();
    setLoading(true);
    setError(null);
    try {
      const xml = tvXmlQuery(API_KEY, trainAnnouncementForStationXML(station, minutesAhead, true));
      const res = await axios.post(API_URL, xml, {
        headers: { "Content-Type": "text/xml" },
        responseType: "json",
      });

      const result = res.data?.RESPONSE?.RESULT?.[0] || {};
      const list = result.TrainAnnouncement ?? [];
      setRows(list.map(toRow));

      const url = result.INFO?.SSEURL || null;
      if (!url) {
        const msg = "No SSEURL returned — adjust filters and try again.";
        setError(msg);
        onError?.(msg);
        return;
      }
      setSseUrl(url);

      const es = new EventSource(url);
      esRef.current = es;

      es.onmessage = (evt) => {
        try {
          const payload = JSON.parse(evt.data);
          const arr = payload?.RESPONSE?.RESULT?.[0]?.TrainAnnouncement ?? [];
          if (arr.length) {
            setRows((prev) => [...arr.map(toRow), ...prev].slice(0, 300));
          }
        } catch {}
      };
      es.onerror = () => es.close();
    } catch (e) {
      handleErr(e);
    } finally {
      setLoading(false);
    }
  }

  function stopStreaming() {
    cleanupSse();
    setSseUrl(null);
  }

  function cleanupSse() {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
  }

  function handleErr(e) {
    const msg = e?.response?.data?.RESPONSE?.RESULT?.[0]?.ERROR?.MESSAGE || e?.message || "Request failed";
    setError(msg);
    onError?.(msg);
    console.error("TV API error:", msg);
  }

  useEffect(() => {
    fetchOnce();
    if (autoStart) setTimeout(() => startStreaming(), 0);
    return () => cleanupSse();
  }, [xmlPayload]);

  return (
    <>
      <div className="container py-3">
        {/* --- Controls --- */}
        <div className="d-flex flex-wrap gap-2 mb-3">
          <input
            type="text"
            className="form-control form-control-sm"
            style={{ width: "200px" }}
            value={station}
            onChange={(e) => setStation(e.target.value.trim())}
            placeholder="Station code e.g. Cst, Blg"
          />
          <button className="btn btn-primary btn-sm" onClick={fetchOnce} disabled={loading}>
            Fetch once
          </button>
          <button className="btn btn-success btn-sm" onClick={startStreaming} disabled={loading || !!sseUrl}>
            Start streaming
          </button>
          <button className="btn btn-danger btn-sm" onClick={stopStreaming} disabled={!sseUrl}>
            Stop
          </button>
          {sseUrl && <span className="badge bg-danger align-self-center">LIVE</span>}
        </div>

        {/* --- Alerts --- */}
        {error && (
          <div className="alert alert-danger py-1" role="alert">
            {error}
          </div>
        )}
        {loading && <div className="alert alert-info py-1">Loading…</div>}

        <p className="mb-2">
          Station: <strong>{station}</strong> | Departures loaded: {rows.length}
        </p>

        {/* --- Table --- */}
        <div className="table-responsive">
          <table className="table table-sm table-hover align-middle">
            <thead className="table-light">
              <tr>
                <th>Train</th>
                <th>Time</th>
                <th>Track</th>
                <th>To</th>
                <th>Operator</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.train}-${r.time}-${i}`}>
                  <td>{r.train ?? "?"}</td>
                  <td>{r.time}</td>
                  <td>{r.track ?? "–"}</td>
                  <td>{r.to?.join(", ") ?? "–"}</td>
                  <td>{r.owner ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
