
const API = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

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
};

export async function getSummary() {
  const response = await fetch(`${API}/api/v1/summary`);
  if (!response.ok) throw new Error("Could not load dashboard summary");
  return response.json() as Promise<{ risk_counts: Record<string, number>; demo_notice: string }>;
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
  status: string; created_at: string;
};

export async function getAlerts() {
  const response = await fetch(`${API}/api/v1/alerts`);
  if (!response.ok) throw new Error("Could not load alerts");
  return response.json() as Promise<Alert[]>;
}

export type InfraFeature = {
  type: "Feature";
  geometry: GeoJSON.Geometry;
  properties: { id: number; kind: string; name: string; district: string | null; status: string | null };
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
  nearby_infrastructure: number; priority_score: number;
};

export async function getPriorities() {
  const response = await fetch(`${API}/api/v1/priorities`);
  if (!response.ok) throw new Error("Could not load priorities");
  return response.json() as Promise<Priority[]>;
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
