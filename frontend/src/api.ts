
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
