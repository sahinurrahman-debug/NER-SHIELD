
const API = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// Live push channel (see backend's /ws/live + ConnectionManager) — new alerts and risk-cell
// changes are broadcast the instant they happen, instead of the dashboard polling on a timer.
export function getWsUrl(path: string) {
  return `${API.replace(/^http/, "ws")}${path}`;
}

export type Feature = {
  type: "Feature";
  geometry: GeoJSON.Geometry;
  properties: {
    cell_id: string; district: string; risk_score: number; severity: string;
    rain_24h_mm: number; soil_moisture_pct: number; slope_deg: number;
  };
};

export type Report = {
  id: number; report_type: string; severity: string; description: string;
  latitude: number; longitude: number; district?: string; road_status?: string;
  image_url?: string; reporter_role: string; verification_status: string; created_at: string;
  trust_score: number | null; trust_flags: string | null;
};

export type DistrictsResponse = { states: { state: string; districts: string[] }[]; note: string };

export async function getDistricts() {
  const response = await fetch(`${API}/api/v1/districts`);
  if (!response.ok) throw new Error("Could not load districts");
  return response.json() as Promise<DistrictsResponse>;
}

export async function getRiskCells() {
  const response = await fetch(`${API}/api/v1/risk-cells`);
  if (!response.ok) throw new Error("Could not load risk cells");
  return response.json() as Promise<{ type: "FeatureCollection"; features: Feature[] }>;
}

export async function getReports() {
  const response = await fetch(`${API}/api/v1/reports`);
  if (!response.ok) throw new Error("Could not load reports");
  return response.json() as Promise<Report[]>;
}

export type Alert = {
  id: number; source_type: string; source_id: string | null; district: string | null;
  severity: string; message: string; channel: string; recipients: string | null;
  status: string; satellite_status: string | null; satellite_note: string | null; created_at: string;
};

export type LiveMessage =
  | { type: "alert"; alert: Alert }
  | { type: "risk_cells"; features: Feature[] };

export async function getAlerts() {
  const response = await fetch(`${API}/api/v1/alerts`);
  if (!response.ok) throw new Error("Could not load alerts");
  return response.json() as Promise<Alert[]>;
}

export type InfraFeature = {
  type: "Feature";
  geometry: GeoJSON.Geometry;
  properties: { id: number; kind: string; name: string; district: string | null; status: string | null; population: number | null };
};

export async function getInfrastructure() {
  const response = await fetch(`${API}/api/v1/infrastructure`);
  if (!response.ok) throw new Error("Could not load infrastructure");
  return response.json() as Promise<{ type: "FeatureCollection"; features: InfraFeature[] }>;
}

export async function getRoadStatus() {
  const response = await fetch(`${API}/api/v1/road-status`);
  if (!response.ok) throw new Error("Could not load road status");
  return response.json() as Promise<Record<string, number>>;
}

export type Priority = {
  cell_id: string; district: string; severity: string; risk_score: number;
  nearby_infrastructure: number; population_at_risk: number; priority_score: number;
};

export async function getPriorities() {
  const response = await fetch(`${API}/api/v1/priorities`);
  if (!response.ok) throw new Error("Could not load priorities");
  return response.json() as Promise<{ note: string; priorities: Priority[] }>;
}

export type ForecastPoint = { risk_score: number; severity: string; rain_24h_mm: number; source: string; created_at: string };

export async function getForecast(district: string) {
  const response = await fetch(`${API}/api/v1/forecast?district=${encodeURIComponent(district)}`);
  if (!response.ok) throw new Error("Could not load forecast");
  return response.json() as Promise<{ district: string; note: string; points: ForecastPoint[] }>;
}

export type Outlook = {
  district: string; probability: number | null; severity: string | null;
  trend_per_day?: number | null; days_to_critical: number | null; readings_used?: number; note: string;
};

export async function getOutlook(district: string) {
  const response = await fetch(`${API}/api/v1/outlook?district=${encodeURIComponent(district)}`);
  if (!response.ok) throw new Error("Could not load outlook");
  return response.json() as Promise<Outlook>;
}

export type ShapFactor = { feature: string; value: number | null; impact: number };
export type Explanation = { base_value: number; top_factors: ShapFactor[]; note: string };
export type PredictResponse = {
  probability: number; risk_score: number; severity: string; source: string;
  contributing_factors: string[]; alert_triggered: boolean; risk_cell_id: string | null;
  imputed_fields: string[]; explanation: Explanation | null;
};

export async function runPrediction(input: Record<string, number | string | null>) {
  const response = await fetch(`${API}/api/v1/predict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new Error("Prediction failed — check inputs");
  return response.json() as Promise<PredictResponse>;
}

export type EvacuationRoute = {
  distance_km: number; path: [number, number][]; roads_used: string[];
  destination: string | null; used_partial_block_roads: string[]; start_snapped_km: number; note: string;
};

export async function getEvacuationRoute(fromLat: number, fromLon: number) {
  const response = await fetch(`${API}/api/v1/evacuation-route?from_lat=${fromLat}&from_lon=${fromLon}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}) as { detail?: string });
    throw new Error(body.detail || "Could not compute an evacuation route");
  }
  return response.json() as Promise<EvacuationRoute>;
}

export type NdviChange = {
  cell_id: string; district: string; configured: boolean;
  before_period: { from: string; to: string } | null;
  after_period: { from: string; to: string } | null;
  mean_ndvi_before: number | null; mean_ndvi_after: number | null;
  ndvi_delta: number | null; vegetation_loss_pct: number | null;
  severity: string;
  before_image_url: string | null; after_image_url: string | null;
  cached: boolean; computed_at: string | null;
  note: string;
};

export async function getNdviChange(cellId: string, forceRefresh = false) {
  const response = await fetch(
    `${API}/api/v1/ndvi-change?cell_id=${encodeURIComponent(cellId)}${forceRefresh ? "&force_refresh=true" : ""}`
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}) as { detail?: string });
    throw new Error(body.detail || "Could not load NDVI vegetation-change data");
  }
  return response.json() as Promise<NdviChange>;
}

export function imageUrl(path: string) {
  return `${API}${path}`;
}
