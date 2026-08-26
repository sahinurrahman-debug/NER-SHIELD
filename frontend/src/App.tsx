
import { useEffect, useState } from "react";
import type { Feature as GeoJsonFeature } from "geojson";
import { GeoJSON, MapContainer, TileLayer } from "react-leaflet";
import type { PathOptions } from "leaflet";
import { getReports, getRiskCells, getSummary, type Feature as RiskFeature, type Report } from "./api";

const colours: Record<string, string> = {
  low: "#22c55e", moderate: "#eab308", high: "#f97316", critical: "#dc2626",
};

function style(feature?: GeoJsonFeature): PathOptions {
  const severity = String(feature?.properties?.severity ?? "low");
  return { color: colours[severity] ?? "#64748b", fillOpacity: 0.48, weight: 2 };
}

export default function App() {
  const [cells, setCells] = useState<RiskFeature[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([getSummary(), getRiskCells(), getReports()])
      .then(([summary, cellsResponse, reportsResponse]) => {
        setCounts(summary.risk_counts);
        setNotice(summary.demo_notice);
        setCells(cellsResponse.features);
        setReports(reportsResponse);
      })
      .catch((err: Error) => setError(err.message));
  }, []);
  const geoJsonData: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: cells as GeoJSON.Feature[],
  };
  return (
    <main>
      <header><div><h1>PaharSathi AI</h1><p>NER landslide decision-support dashboard</p></div><span className="badge">DEMO</span></header>
      {notice && <p className="notice">{notice}</p>}
      {error && <p className="error">{error}. Is the API running on port 8000?</p>}
      <section className="cards">
        {["low", "moderate", "high", "critical"].map((level) => (
          <article key={level} className={`card ${level}`}><span>{level}</span><strong>{counts[level] ?? 0}</strong></article>
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
          
          </MapContainer>
        </div>
        <aside><h2>Recent field reports</h2>{reports.length === 0 && <p>No reports yet.</p>}
          {reports.map((report) => <article className="report" key={report.id}>
            <b>{report.severity.toUpperCase()} · {report.report_type.replace("_", " ")}</b>
            <p>{report.description}</p><small>{report.district ?? "Unknown district"} · {report.road_status ?? "road status not set"}</small>
          </article>)}
        </aside>
      </section>
    </main>
  );
}