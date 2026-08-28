
import { useEffect, useState } from "react";
import type { Feature as GeoJsonFeature } from "geojson";
import { GeoJSON, MapContainer, TileLayer } from "react-leaflet";
import { CircleMarker } from "leaflet";
import type { PathOptions } from "leaflet";
import {
  getAlerts, getForecast, getInfrastructure, getPriorities, getReports, getRiskCells, getRoadStatus, getSummary,
  type Alert, type Feature as RiskFeature, type ForecastPoint, type InfraFeature, type Priority, type Report,
} from "./api";

const colours: Record<string, string> = {
  low: "#22c55e", moderate: "#eab308", high: "#f97316", critical: "#dc2626",
};
const roadColours: Record<string, string> = {
  open: "#22c55e", restricted: "#eab308", partial_block: "#f97316", blocked: "#dc2626",
};

function style(feature?: GeoJsonFeature): PathOptions {
  const severity = String(feature?.properties?.severity ?? "low");
  return { color: colours[severity] ?? "#64748b", fillOpacity: 0.48, weight: 2 };
}

function infraStyle(feature?: GeoJsonFeature): PathOptions {
  const status = String(feature?.properties?.status ?? "");
  return { color: roadColours[status] ?? "#38bdf8", weight: 4, dashArray: "6 4" };
}

export default function App() {
  const [cells, setCells] = useState<RiskFeature[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [infra, setInfra] = useState<InfraFeature[]>([]);
  const [roadCounts, setRoadCounts] = useState<Record<string, number>>({});
  const [priorities, setPriorities] = useState<Priority[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [forecastDistrict, setForecastDistrict] = useState("East Khasi Hills");
  const [forecastPoints, setForecastPoints] = useState<ForecastPoint[]>([]);
  const [forecastNote, setForecastNote] = useState("");

  useEffect(() => {
    Promise.all([getSummary(), getRiskCells(), getReports(), getAlerts(), getInfrastructure(), getRoadStatus(), getPriorities()])
      .then(([summary, cellsResponse, reportsResponse, alertsResponse, infraResponse, roadStatusResponse, prioritiesResponse]) => {
        setCounts(summary.risk_counts);
        setNotice(summary.demo_notice);
        setCells(cellsResponse.features);
        setReports(reportsResponse);
        setAlerts(alertsResponse);
        setInfra(infraResponse.features);
        setRoadCounts(roadStatusResponse);
        setPriorities(prioritiesResponse);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  function loadForecast() {
    getForecast(forecastDistrict)
      .then((data) => { setForecastPoints(data.points); setForecastNote(data.note); })
      .catch((err: Error) => setError(err.message));
  }

  const geoJsonData: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: cells as GeoJSON.Feature[],
  };
  const infraGeoJsonData: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: infra as GeoJSON.Feature[],
  };
  return (
    <main>
      <header><div><h1>NER-SHIELD</h1><p>NER landslide decision-support dashboard</p></div><span className="badge">DEMO</span></header>
      {notice && <p className="notice">{notice}</p>}
      {error && <p className="error">{error}. Is the API running on port 8000?</p>}
      <section className="cards">
        {["low", "moderate", "high", "critical"].map((level) => (
          <article key={level} className={`card ${level}`}><span>{level}</span><strong>{counts[level] ?? 0}</strong></article>
        ))}
      </section>
      <section className="cards">
        {["open", "restricted", "partial_block", "blocked"].map((status) => (
          <article key={status} className={`card road-${status}`}>
            <span>{status.replace("_", " ")}</span><strong>{roadCounts[status] ?? 0}</strong>
          </article>
        ))}
      </section>
      <section className="alerts-panel">
        <h2>Recent alerts</h2>
        {alerts.length === 0 && <p>No alerts triggered yet.</p>}
        {alerts.map((alert) => (
          <article className={`alert-row ${alert.severity}`} key={alert.id}>
            <span className={`alert-status ${alert.status}`}>{alert.status.replace("_", " ")}</span>
            <div>
              <b>{alert.severity.toUpperCase()} · {alert.district ?? "Unknown location"}</b>
              <p>{alert.message}</p>
            </div>
          </article>
        ))}
      </section>
      <section className="layout">
        <div className="map-wrap">
          <MapContainer center={[25.58, 91.885]} zoom={13} className="map">
            <TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
            <GeoJSON data={geoJsonData} style={style}
            onEachFeature={(feature, layer) => {
            const p = feature.properties as RiskFeature["properties"];
            layer.bindPopup(
            `<b>${p.severity.toUpperCase()}</b><br/>${p.district}<br/>Score: ${p.risk_score}`
          );
        }}
      />
          <GeoJSON
            data={infraGeoJsonData}
            style={infraStyle}
            pointToLayer={(feature, latlng) => {
              const p = feature.properties as InfraFeature["properties"];
              const kindColours: Record<string, string> = { village: "#a3e635", hospital: "#f472b6", school: "#60a5fa" };
              return new CircleMarker(latlng, { radius: 6, color: kindColours[p.kind] ?? "#e2e8f0", fillOpacity: 0.9 });
            }}
            onEachFeature={(feature, layer) => {
              const p = feature.properties as InfraFeature["properties"];
              layer.bindPopup(`<b>${p.name}</b><br/>${p.kind}${p.status ? ` · ${p.status}` : ""}`);
            }}
          />
          </MapContainer>
        </div>
        <aside><h2>Recent field reports</h2>{reports.length === 0 && <p>No reports yet.</p>}
          {reports.map((report) => <article className="report" key={report.id}>
            <b>{report.severity.toUpperCase()} · {report.report_type.replace("_", " ")}</b>
            <p>{report.description}</p><small>{report.district ?? "Unknown district"} · {report.road_status ?? "road status not set"}</small>
          </article>)}
        </aside>
      </section>
      <section className="layout">
        <div className="table-wrap">
          <h2>Emergency response prioritisation</h2>
          <table className="priority-table">
            <thead><tr><th>District</th><th>Severity</th><th>Risk score</th><th>Nearby infra</th><th>Priority score</th></tr></thead>
            <tbody>
              {priorities.map((p) => (
                <tr key={p.cell_id} className={p.severity}>
                  <td>{p.district}</td><td>{p.severity}</td><td>{p.risk_score}</td>
                  <td>{p.nearby_infrastructure}</td><td>{p.priority_score}</td>
                </tr>
              ))}
              {priorities.length === 0 && <tr><td colSpan={5}>No risk cells yet.</td></tr>}
            </tbody>
          </table>
        </div>
        <aside>
          <h2>Weather-linked risk forecast</h2>
          <div className="forecast-controls">
            <input value={forecastDistrict} onChange={(e) => setForecastDistrict(e.target.value)} placeholder="District name" />
            <button onClick={loadForecast}>Load</button>
          </div>
          {forecastNote && <p className="forecast-note">{forecastNote}</p>}
          {forecastPoints.map((point, i) => (
            <div className={`forecast-point ${point.severity}`} key={i}>
              <b>{point.risk_score}%</b> · {point.severity} · {point.rain_24h_mm.toFixed(0)}mm rain · {point.source}
              <small> {new Date(point.created_at).toLocaleString()}</small>
            </div>
          ))}
          {forecastPoints.length === 0 && <p>No readings logged for this district yet — submit a prediction with a district set first.</p>}
        </aside>
      </section>
    </main>
  );
}