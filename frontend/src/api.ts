
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
