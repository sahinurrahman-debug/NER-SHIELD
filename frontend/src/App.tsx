
import { useEffect, useRef, useState } from "react";
import type { Feature as GeoJsonFeature } from "geojson";
import { Circle, CircleMarker as RLCircleMarker, GeoJSON, MapContainer, Polyline, Popup, TileLayer, useMap, useMapEvents } from "react-leaflet";
import { CircleMarker } from "leaflet";
import type { Map as LeafletMap, PathOptions } from "leaflet";
import {
  getAlerts, getEvacuationRoute, getForecast, getInfrastructure, getNdviChange, getOutlook, getPriorities, getReports,
  getRiskCells, getRoadStatus, getSummary, imageUrl, runPrediction,
  type Alert, type EvacuationRoute, type Feature as RiskFeature, type ForecastPoint, type InfraFeature, type NdviChange,
  type Outlook, type PredictResponse, type Priority, type Report,
} from "./api";

const colours: Record<string, string> = {
  low: "#22c55e", moderate: "#eab308", high: "#f97316", critical: "#dc2626",
};
const roadColours: Record<string, string> = {
  open: "#22c55e", restricted: "#eab308", partial_block: "#f97316", blocked: "#dc2626",
};
// Real-world radius (metres) for each severity's alert zone circle on the map.
const ZONE_RADIUS_M: Record<string, number> = {
  low: 300, moderate: 450, high: 650, critical: 900,
};

function style(feature?: GeoJsonFeature): PathOptions {
  const severity = String(feature?.properties?.severity ?? "low");
  return { color: colours[severity] ?? "#64748b", fillOpacity: 0.48, weight: 2 };
}

function infraStyle(feature?: GeoJsonFeature): PathOptions {
  const status = String(feature?.properties?.status ?? "");
  return { color: roadColours[status] ?? "#38bdf8", weight: 4, dashArray: "6 4" };
}

// Averages a GeoJSON geometry's coordinates down to one [lat, lon] point, so any
// risk-cell polygon or infrastructure line/point can be flown to on the map.
function geometryCenter(geometry: GeoJSON.Geometry): [number, number] | null {
  const flat: [number, number][] = [];
  const collect = (coords: unknown): void => {
    if (Array.isArray(coords) && typeof coords[0] === "number") {
      flat.push([coords[1] as number, coords[0] as number]); // GeoJSON is [lon, lat]
    } else if (Array.isArray(coords)) {
      coords.forEach(collect);
    }
  };
  collect((geometry as { coordinates?: unknown }).coordinates);
  if (flat.length === 0) return null;
  const lat = flat.reduce((sum, p) => sum + p[0], 0) / flat.length;
  const lon = flat.reduce((sum, p) => sum + p[1], 0) / flat.length;
  return [lat, lon];
}

function MapController({ onReady }: { onReady: (map: LeafletMap) => void }) {
  const map = useMap();
  useEffect(() => onReady(map), [map, onReady]);
  return null;
}

function MapClickHandler({ onClick }: { onClick: (lat: number, lon: number) => void }) {
  useMapEvents({ click: (e) => onClick(e.latlng.lat, e.latlng.lng) });
  return null;
}

// Resolves an alert down to one [lat, lon] point, regardless of which path triggered
// it — a monitored cell (by cell_id), a field report (by its own coordinates), or a
// prediction alert (falls back to its district's highest-scoring cell).
function locateAlert(alert: Alert, cells: RiskFeature[], reports: Report[]): [number, number] | null {
  if (alert.source_type === "monitor" && alert.source_id) {
    const cell = cells.find((c) => c.properties.cell_id === alert.source_id);
    const center = cell && geometryCenter(cell.geometry);
    if (center) return center;
  }
  if (alert.source_type === "field_report" && alert.source_id) {
    const report = reports.find((r) => r.id === Number(alert.source_id));
    if (report) return [report.latitude, report.longitude];
  }
  const districtCells = cells.filter((c) => c.properties.district === alert.district);
  const best = districtCells.sort((a, b) => b.properties.risk_score - a.properties.risk_score)[0];
  return best ? geometryCenter(best.geometry) : null;
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
  const [outlook, setOutlook] = useState<Outlook | null>(null);
  const [shapInputs, setShapInputs] = useState({ Rainfall_mm: 150, Slope_Angle: 40, Soil_Saturation: 0.5, Vegetation_Cover: 0.5 });
  const [predictResult, setPredictResult] = useState<PredictResponse | null>(null);
  const [predictBusy, setPredictBusy] = useState(false);
  const [predictError, setPredictError] = useState("");
  const [evacStart, setEvacStart] = useState<[number, number] | null>(null);
  const [evacRoute, setEvacRoute] = useState<EvacuationRoute | null>(null);
  const [evacBusy, setEvacBusy] = useState(false);
  const [evacError, setEvacError] = useState("");
  const [ndviCellId, setNdviCellId] = useState("");
  const [ndviResult, setNdviResult] = useState<NdviChange | null>(null);
  const [ndviBusy, setNdviBusy] = useState(false);
  const [ndviError, setNdviError] = useState("");
  const mapRef = useRef<LeafletMap | null>(null);

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
        setPriorities(prioritiesResponse.priorities);
        if (cellsResponse.features.length > 0) {
          setNdviCellId((cellsResponse.features[0] as RiskFeature).properties.cell_id);
        }
      })
      .catch((err: Error) => setError(err.message));
    getOutlook("East Khasi Hills").then(setOutlook).catch(() => {});
  }, []);

  function loadForecast() {
    getForecast(forecastDistrict)
      .then((data) => { setForecastPoints(data.points); setForecastNote(data.note); })
      .catch((err: Error) => setError(err.message));
    getOutlook(forecastDistrict)
      .then(setOutlook)
      .catch((err: Error) => setError(err.message));
  }

  function flyToAlert(alert: Alert) {
    const map = mapRef.current;
    const center = locateAlert(alert, cells, reports);
    if (map && center) map.flyTo(center, 15);
  }

  function planEvacuationRoute(lat: number, lon: number) {
    setEvacStart([lat, lon]);
    setEvacRoute(null);
    setEvacError("");
    setEvacBusy(true);
    getEvacuationRoute(lat, lon)
      .then(setEvacRoute)
      .catch((err: Error) => setEvacError(err.message))
      .finally(() => setEvacBusy(false));
  }

  function loadNdviChange(forceRefresh = false) {
    if (!ndviCellId) return;
    setNdviBusy(true);
    setNdviError("");
    getNdviChange(ndviCellId, forceRefresh)
      .then(setNdviResult)
      .catch((err: Error) => setNdviError(err.message))
      .finally(() => setNdviBusy(false));
  }

  function runExplainablePrediction() {
    setPredictBusy(true);
    setPredictError("");
    // Deliberately omits district/lat/lon — a pure explainability sandbox that never
    // creates alerts, map cells, or forecast entries, so it's safe to click repeatedly.
    runPrediction(shapInputs)
      .then(setPredictResult)
      .catch((err: Error) => setPredictError(err.message))
      .finally(() => setPredictBusy(false));
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
      <header>
        <div><h1>NER-SHIELD</h1><p>NER landslide decision-support dashboard</p></div>
        <div className="header-right">
          <span className="live-dot" /><span className="live-label">LIVE</span>
          <span className="badge">DEMO</span>
        </div>
      </header>
      {notice && <p className="notice">{notice}</p>}
      {error && <p className="error">{error}. Is the API running on port 8000?</p>}

      <section className="hero">
        <div className="map-wrap hero-map">
          <MapContainer center={[25.58, 91.885]} zoom={13} className="map">
            <MapController onReady={(map) => { mapRef.current = map; }} />
            <MapClickHandler onClick={planEvacuationRoute} />
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
              layer.bindPopup(
                `<b>${p.name}</b><br/>${p.kind}${p.status ? ` · ${p.status}` : ""}` +
                (p.population ? `<br/>~${p.population.toLocaleString()} people (approx.)` : "")
              );
            }}
          />
          {alerts.map((alert) => {
            const center = locateAlert(alert, cells, reports);
            if (!center) return null;
            return (
              <Circle
                key={`zone-${alert.id}`}
                center={center}
                radius={ZONE_RADIUS_M[alert.severity] ?? 300}
                pathOptions={{
                  color: colours[alert.severity] ?? "#64748b", weight: 2, dashArray: "8 6",
                  fillOpacity: 0.1, fillColor: colours[alert.severity] ?? "#64748b",
                }}
                eventHandlers={{ click: () => flyToAlert(alert) }}
              >
                <Popup>
                  <b>{alert.severity.toUpperCase()} ALERT ZONE</b><br />
                  {alert.district ?? "Unknown location"}<br />
                  Status: {alert.status.replace("_", " ")}
                </Popup>
              </Circle>
            );
          })}
          {evacStart && (
            <RLCircleMarker center={evacStart} radius={7} pathOptions={{ color: "#38bdf8", fillOpacity: 1, weight: 2 }}>
              <Popup>Evacuation start point</Popup>
            </RLCircleMarker>
          )}
          {evacRoute && (
            <Polyline
              positions={evacRoute.path.map(([lon, lat]) => [lat, lon] as [number, number])}
              pathOptions={{ color: "#22d3ee", weight: 5, opacity: 0.9, dashArray: evacRoute.used_partial_block_roads.length ? "4 6" : undefined }}
            />
          )}
          </MapContainer>
        </div>
        <aside className="alerts-panel hero-alerts">
          <h2 className="panel-title"><span className="dot" />Recent alerts</h2>
          <p className="forecast-note">
            SMS is the primary channel; when it can't confirm real delivery — the exact scenario
            where a landslide has taken out both the cell tower and local internet — a satellite
            fallback (🛰) is attempted over Iridium to relay modems at village/relay points, so
            critical alerts can still get through with zero terrestrial network.
          </p>
          {alerts.length === 0 && <p>No alerts triggered yet.</p>}
          {alerts.map((alert) => (
            <article
              className={`alert-row ${alert.severity} clickable`}
              key={alert.id}
              onClick={() => flyToAlert(alert)}
              title="Click to jump to this location on the map"
            >
              <span className={`alert-status ${alert.status}`}>{alert.status.replace("_", " ")}</span>
              <div>
                <b>{alert.severity.toUpperCase()} · {alert.district ?? "Unknown location"}</b>
                <p>{alert.message}</p>
                {alert.satellite_status && (
                  <span
                    className={`alert-status satellite-status satellite-${alert.satellite_status}`}
                    title={alert.satellite_note ?? ""}
                  >
                    🛰 satellite: {alert.satellite_status.replace("_", " ")}
                  </span>
                )}
              </div>
            </article>
          ))}
        </aside>
      </section>

      <section className="stat-strip">
        <div className="stat-group">
          <h3 className="stat-group-title">Risk severity</h3>
          <div className="cards">
            {["low", "moderate", "high", "critical"].map((level) => (
              <article key={level} className={`card ${level}`}><span>{level}</span><strong>{counts[level] ?? 0}</strong></article>
            ))}
          </div>
        </div>
        <div className="stat-group">
          <h3 className="stat-group-title">Road connectivity</h3>
          <div className="cards">
            {["open", "restricted", "partial_block", "blocked"].map((status) => (
              <article key={status} className={`card road-${status}`}>
                <span>{status.replace("_", " ")}</span><strong>{roadCounts[status] ?? 0}</strong>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="sub-panel explainability-panel">
        <h2 className="panel-title"><span className="dot" />AI explainability — try a live prediction</h2>
        <p className="forecast-note">
          Adjust the 4 readings that actually drive this model, run a real prediction, and see exactly how much
          each one pushed the risk score up or down — genuine SHAP values from the trained model's own decision
          trees, not a canned explanation. This is a sandbox: it never creates alerts or map cells.
        </p>
        <div className="shap-inputs">
          <label>Rainfall (mm)
            <input type="number" value={shapInputs.Rainfall_mm}
              onChange={(e) => setShapInputs({ ...shapInputs, Rainfall_mm: Number(e.target.value) })} />
          </label>
          <label>Slope angle (°)
            <input type="number" value={shapInputs.Slope_Angle}
              onChange={(e) => setShapInputs({ ...shapInputs, Slope_Angle: Number(e.target.value) })} />
          </label>
          <label>Soil saturation (0–1)
            <input type="number" step="0.01" min="0" max="1" value={shapInputs.Soil_Saturation}
              onChange={(e) => setShapInputs({ ...shapInputs, Soil_Saturation: Number(e.target.value) })} />
          </label>
          <label>Vegetation cover (0–1)
            <input type="number" step="0.01" min="0" max="1" value={shapInputs.Vegetation_Cover}
              onChange={(e) => setShapInputs({ ...shapInputs, Vegetation_Cover: Number(e.target.value) })} />
          </label>
        </div>
        <button onClick={runExplainablePrediction} disabled={predictBusy}>
          {predictBusy ? "Running…" : "Run prediction"}
        </button>
        {predictError && <p className="error">{predictError}</p>}
        {predictResult && (
          <div className="predict-result">
            <div className="outlook-main">
              <div className="outlook-probability">
                <strong>{Math.round(predictResult.probability * 100)}%</strong>
                <span>risk score {predictResult.risk_score} · source: {predictResult.source}</span>
              </div>
              <span className={`alert-status severity-badge ${predictResult.severity}`}>{predictResult.severity}</span>
            </div>
            {predictResult.explanation ? (
              <>
                <div className="shap-chart">
                  {predictResult.explanation.top_factors.map((f) => {
                    const maxAbs = Math.max(...predictResult.explanation!.top_factors.map((x) => Math.abs(x.impact)), 0.0001);
                    const pct = (Math.abs(f.impact) / maxAbs) * 50;
                    const positive = f.impact >= 0;
                    return (
                      <div className="shap-row" key={f.feature}>
                        <span className="shap-label">{f.feature}{f.value !== null ? ` (${f.value})` : ""}</span>
                        <div className="shap-track">
                          <div className="shap-center-line" />
                          <div
                            className="shap-fill"
                            style={{ width: `${pct}%`, left: positive ? "50%" : `${50 - pct}%`, background: positive ? "#f87171" : "#4ade80" }}
                          />
                        </div>
                        <span className="shap-value">{f.impact >= 0 ? "+" : ""}{f.impact}</span>
                      </div>
                    );
                  })}
                </div>
                <p className="forecast-note">{predictResult.explanation.note}</p>
              </>
            ) : (
              <>
                <p className="forecast-note">
                  SHAP explanation unavailable for this prediction (source: {predictResult.source}). Contributing factors:
                </p>
                <ul>{predictResult.contributing_factors.map((f) => <li key={f}>{f}</li>)}</ul>
              </>
            )}
          </div>
        )}
      </section>

      <section className="sub-panel ndvi-panel">
        <h2 className="panel-title"><span className="dot" />Satellite vegetation change — real Sentinel-2 NDVI</h2>
        <p className="forecast-note">
          Real Sentinel-2 L2A satellite imagery pulled live from Sentinel Hub — not a proxy input — comparing
          this risk cell's vegetation cover now against the same season one year ago. Deforestation is a leading
          landslide indicator: it strips the root cohesion that holds slope soil in place.
        </p>
        <div className="forecast-controls">
          <select value={ndviCellId} onChange={(e) => setNdviCellId(e.target.value)}>
            {Array.from(new Set(cells.map((c) => c.properties.cell_id))).map((id) => {
              const cell = cells.find((c) => c.properties.cell_id === id);
              return <option key={id} value={id}>{id}{cell ? ` · ${cell.properties.district}` : ""}</option>;
            })}
          </select>
          <button onClick={() => loadNdviChange(false)} disabled={ndviBusy || !ndviCellId}>
            {ndviBusy ? "Analyzing…" : "Analyze vegetation change"}
          </button>
        </div>
        {ndviError && <p className="error">{ndviError}</p>}
        {ndviResult && (
          <div className="ndvi-result">
            {ndviResult.mean_ndvi_before !== null && ndviResult.mean_ndvi_after !== null && (
              <>
                <div className="outlook-main">
                  <div className="outlook-probability">
                    <strong>{ndviResult.ndvi_delta! >= 0 ? "+" : ""}{ndviResult.ndvi_delta}</strong>
                    <span>NDVI change · before {ndviResult.mean_ndvi_before} → after {ndviResult.mean_ndvi_after}</span>
                  </div>
                  <span className={`alert-status severity-badge ndvi-${ndviResult.severity.replace(/ /g, "-")}`}>
                    {ndviResult.severity}
                  </span>
                </div>
                {ndviResult.vegetation_loss_pct !== null && ndviResult.vegetation_loss_pct > 0 && (
                  <p className="forecast-note">Estimated vegetation loss: <b>{ndviResult.vegetation_loss_pct}%</b></p>
                )}
              </>
            )}
            {(ndviResult.before_image_url || ndviResult.after_image_url) && (
              <div className="ndvi-images">
                <div className="ndvi-img-wrap">
                  <small>Before ({ndviResult.before_period?.from.slice(0, 10)} to {ndviResult.before_period?.to.slice(0, 10)})</small>
                  {ndviResult.before_image_url
                    ? <img src={imageUrl(ndviResult.before_image_url)} alt="NDVI before" />
                    : <div className="ndvi-img-missing">No scene available</div>}
                </div>
                <div className="ndvi-img-wrap">
                  <small>After ({ndviResult.after_period?.from.slice(0, 10)} to {ndviResult.after_period?.to.slice(0, 10)})</small>
                  {ndviResult.after_image_url
                    ? <img src={imageUrl(ndviResult.after_image_url)} alt="NDVI after" />
                    : <div className="ndvi-img-missing">No scene available</div>}
                </div>
              </div>
            )}
            <p className="forecast-note">{ndviResult.note}</p>
            {ndviResult.cached && ndviResult.computed_at && (
              <p className="forecast-note">
                Cached from {new Date(ndviResult.computed_at).toLocaleString()} —{" "}
                <button className="link-button" onClick={() => loadNdviChange(true)} disabled={ndviBusy}>refresh now</button>
              </p>
            )}
          </div>
        )}
        {!ndviResult && !ndviBusy && <p>Pick a risk cell and click Analyze to pull real Sentinel-2 imagery for it.</p>}
      </section>

      <section className="layout">
        <div className="table-wrap">
          <h2 className="panel-title"><span className="dot" />Emergency response prioritisation</h2>
          <table className="priority-table">
            <thead><tr><th>District</th><th>Severity</th><th>Risk score</th><th>Nearby infra</th><th>Population at risk</th><th>Priority score</th></tr></thead>
            <tbody>
              {priorities.map((p) => (
                <tr key={p.cell_id} className={p.severity}>
                  <td>{p.district}</td><td>{p.severity}</td><td>{p.risk_score}</td>
                  <td>{p.nearby_infrastructure}</td>
                  <td>{p.population_at_risk > 0 ? `~${p.population_at_risk.toLocaleString()}` : "—"}</td>
                  <td>{p.priority_score}</td>
                </tr>
              ))}
              {priorities.length === 0 && <tr><td colSpan={6}>No risk cells yet.</td></tr>}
            </tbody>
          </table>
          <p className="forecast-note">Population figures are approximate named-settlement estimates, not live census data.</p>
        </div>
        <aside className="secondary-aside">
          <div className="sub-panel">
            <h2 className="panel-title"><span className="dot" />Evacuation route planner</h2>
            <p className="forecast-note">
              Click anywhere on the map to plan the shortest route to the nearest hospital — a real
              Dijkstra shortest-path search over the local road network. Blocked roads are excluded
              entirely; the route reroutes live if a road's status changes.
            </p>
            {evacBusy && <p>Computing route…</p>}
            {evacError && <p className="error">{evacError}</p>}
            {evacRoute && (
              <div className="evac-result">
                <div className="outlook-main">
                  <div className="outlook-probability">
                    <strong>{evacRoute.distance_km} km</strong>
                    <span>to {evacRoute.destination ?? "destination"}</span>
                  </div>
                  {evacRoute.used_partial_block_roads.length > 0 && (
                    <span className="alert-status simulated">uses restricted road</span>
                  )}
                </div>
                <p className="forecast-note">Via: {evacRoute.roads_used.join(" → ")}</p>
                {evacRoute.used_partial_block_roads.length > 0 && (
                  <p className="forecast-note critical-text">
                    Caution: route includes a partially blocked segment ({evacRoute.used_partial_block_roads.join(", ")}) — no clearer path was available.
                  </p>
                )}
                <p className="forecast-note">{evacRoute.note}</p>
              </div>
            )}
          </div>
          <div className="sub-panel outlook-panel">
            <h2 className="panel-title"><span className="dot" />Risk probability &amp; outlook</h2>
            <div className="forecast-controls">
              <input value={forecastDistrict} onChange={(e) => setForecastDistrict(e.target.value)} placeholder="District name" />
              <button onClick={loadForecast}>Load</button>
            </div>
            {outlook && (
              <div className="outlook-body">
                <div className="outlook-main">
                  <div className="outlook-probability">
                    <strong>{outlook.probability !== null ? `${Math.round(outlook.probability * 100)}%` : "—"}</strong>
                    <span>predicted probability within 7 days</span>
                  </div>
                  {outlook.severity && (
                    <span className={`alert-status severity-badge ${outlook.severity}`}>{outlook.severity}</span>
                  )}
                </div>
                <div className="outlook-days">
                  {outlook.days_to_critical === null && <span>No clear worsening trend toward critical yet.</span>}
                  {outlook.days_to_critical === 0 && <span className="critical-text">Already at or above critical.</span>}
                  {outlook.days_to_critical !== null && outlook.days_to_critical > 0 && (
                    <span>Estimated <b>{outlook.days_to_critical}</b> day(s) to critical at current trend.</span>
                  )}
                </div>
                <p className="forecast-note">{outlook.note}</p>
              </div>
            )}
            {!outlook && <p>Enter a district and click Load.</p>}
          </div>
          <div className="sub-panel">
            <h2 className="panel-title"><span className="dot" />Weather-linked risk forecast</h2>
            {forecastNote && <p className="forecast-note">{forecastNote}</p>}
            {forecastPoints.map((point, i) => (
              <div className={`forecast-point ${point.severity}`} key={i}>
                <b>{point.risk_score}%</b> · {point.severity} · {point.rain_24h_mm.toFixed(0)}mm rain · {point.source}
                <small> {new Date(point.created_at).toLocaleString()}</small>
              </div>
            ))}
            {forecastPoints.length === 0 && <p>No readings logged for this district yet — submit a prediction with a district set first.</p>}
          </div>
          <div className="sub-panel">
            <h2 className="panel-title"><span className="dot" />Recent field reports</h2>
            {reports.length === 0 && <p>No reports yet.</p>}
            {reports.map((report) => <article className="report" key={report.id}>
              <b>{report.severity.toUpperCase()} · {report.report_type.replace("_", " ")}</b>
              <p>{report.description}</p><small>{report.district ?? "Unknown district"} · {report.road_status ?? "road status not set"}</small>
              {report.trust_score !== null && (
                <div className="trust-row" title={report.trust_flags ?? ""}>
                  <span className={`trust-badge ${report.trust_score >= 70 ? "trust-high" : report.trust_score >= 40 ? "trust-medium" : "trust-low"}`}>
                    Photo trust {report.trust_score}%
                  </span>
                  {report.trust_flags && <small className="trust-flags">{report.trust_flags}</small>}
                </div>
              )}
            </article>)}
          </div>
        </aside>
      </section>
    </main>
  );
}