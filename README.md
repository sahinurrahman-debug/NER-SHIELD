# NER-SHIELD

**AI-based landslide early-warning and disaster-response platform for the North Eastern Region (NER) of India** 


**Live deployment:**
- Dashboard: https://ner-shield-dashboard.onrender.com
- API + interactive docs: https://ner-shield-api.onrender.com/docs

---

## Table of contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Repo layout](#repo-layout)
- [Feature walkthrough](#feature-walkthrough)
- [Running locally](#running-locally)
- [Key environment variables](#key-environment-variables)
- [API overview](#api-overview)
- [Data & ML model](#data--ml-model)
- [Mobile app](#mobile-app)
- [Known limitations](#known-limitations)

## What it does

- **Predicts landslide risk** from rainfall, slope, soil saturation, vegetation cover, historical lanslide count and 29 other sensor-style inputs using a trained XGBoost model (with a rule-based fallback if the model can't load), including a SHAP-based explainability breakdown of which factors drove a given prediction.
- **Covers all 8 NER states / 130 districts** —  Every district is geocoded and seeded with a baseline risk cell on first startup, so a prediction for *any* NER district immediately shows up on the map, in the emergency table, and in the outlook panel.
- **Pulls real, live weather data** (rainfall + soil moisture via Open-Meteo, free/keyless) on a timer and automatically rescores risk.
- **Renders a live GIS dashboard**: risk heatmap, real infrastructure layer (hospitals, schools, key public buildings, major roads — seeded NER-wide from OpenStreetMap), road-connectivity status, weather-linked risk trend + statistical outlook (real least-squares regression, not a canned message), satellite NDVI vegetation-change detection (real Sentinel-2 imagery), and an emergency-response prioritisation ranking backed by real Census 2011 population data.
- **Pushes updates live** over WebSocket (`/ws/live`) — new risk cells, predictions, and alerts appear on every connected dashboard instantly, no polling.
- **Plans evacuation routes** for any location in NER: a real local road-network graph (Dijkstra, with simulated blocked-road detours) falling back to live OSRM routing + nearest real hospital lookup (via OpenStreetMap Overpass) everywhere else in the region.
- **Lets citizens/field officers submit geo-tagged photo/video reports** of cracks, slope movement, or blocked roads from a mobile app, with offline queuing and a basic trust/spam score.
- **Fires multilingual (English/Hindi/Assamese/Bengali) alerts** automatically when risk crosses a threshold — from field reports, predictions, or the live monitoring loop — via SMS (Textbee, using an Android phone's own SIM) with cooldown to avoid spam, and a **Satellite Fallback** (Rock7 RockBLOCK / Iridium SBD) for when a landslide has taken down both the local cell tower and the internet.

## Architecture

```
Open-Meteo (live weather)     OpenStreetMap Overpass      Sentinel Hub (NDVI)      OSRM (routing)
        |                          |                              |                       |
        v                          v                              v                       v
                          FastAPI backend (Python, Docker on Render)
                          - PostGIS: risk cells, infrastructure, reports, alerts, prediction log, NDVI cache
                          - Trained XGBoost model (risk_model.joblib) + rule-based fallback + SHAP explainability
                          - Background monitor loop (live rescoring every 120s)
                          - One-time background seed tasks (hospitals, schools/roads/buildings, district baselines)
                          - Alert pipeline: multilingual SMS (Textbee) -> satellite fallback (RockBLOCK)
                          - WebSocket broadcast (/ws/live) for real-time dashboard updates
                                      |                                  |
                                      v                                  v
                    React + Leaflet dashboard (Render Static Site)   Flutter mobile app (Android)
                    - GIS map, alerts, prioritisation, outlook       - Field reports (photo/video, offline queue)
                    - Evacuation planner, NDVI panel, SHAP sandbox   - Alerts feed, district picker
```

| Piece | Stack | Deployed as |
|---|---|---|
| `backend/` | FastAPI, SQLAlchemy, GeoAlchemy2, PostGIS, XGBoost/scikit-learn, SHAP, NetworkX | Render Web Service (Docker) |
| `frontend/` | React, TypeScript, Vite, Leaflet/react-leaflet, PWA | Render Static Site |
| `mobile/` | Flutter (Android) | Sideloaded release APK |
| `ml/` | Training notebook + dataset (Colab) | — |

## Repo layout

```
ner-shield/
  backend/       FastAPI + PostGIS API, ML inference, alert pipeline, seed/background tasks
  frontend/      React GIS dashboard (Vite, TypeScript, Leaflet)
  mobile/        Flutter field-reporting + alerts app (Android)
  ml/            Training data + Colab notebook
  infra/         Local PostGIS init SQL
  compose.yml    Local dev stack (API + Postgres/PostGIS)
```

## Feature walkthrough

### Risk prediction & explainability
`POST /api/v1/predict` runs the trained model on up to 34 sensor-style features. Called with `latitude`/`longitude` it places/updates a live risk cell on the map; called with `district` it updates that district's baseline cell directly (auto-geocoding a first-time cell if needed) — so **every prediction for every NER district shows up in the Emergency Response Prioritisation table**, not just the pilot area. A separate explainability sandbox on the dashboard runs SHAP against the same model without writing to the map, so it's safe to experiment with.

### NER-wide district coverage
`GET /api/v1/districts` serves all 130 districts across the 8 NER states (`NER_DISTRICTS` in `backend/app/main.py`). A background task (`seed_district_baseline_cells`) geocodes and seeds a neutral baseline cell for every district on first startup (rate-limited to Nominatim's 1 req/sec policy, with a Photon fallback), so no district is ever "missing" from the map.

### Real infrastructure, NER-wide
Three background seed tasks populate the `infrastructure` table from live OpenStreetMap data across the full NER bounding box:
- **Hospitals** (`seed_ner_hospitals`) — ~3,000 real hospitals, used by the evacuation planner's nearest-hospital lookup.
- **Schools, key public buildings, and major roads** (`seed_ner_infrastructure`) — real schools, colleges/town halls/community centres/marketplaces, and motorway/trunk/primary roads, fetched as lightweight center-points (not full road geometry) with a hard result cap and chunked commits, specifically designed to stay within Render's free-tier 512MB RAM limit.

All seeds are idempotent (skip once populated), run as background tasks so they never block startup, and degrade gracefully (retry next restart) if OpenStreetMap's Overpass API is unreachable — with automatic fallback across three independent Overpass mirrors.

### Emergency Response Prioritisation
`GET /api/v1/priorities` ranks every risk cell by:

```
priority_score = risk_score * (1 + 0.2 * nearby_infrastructure + 0.05 * population_at_risk / 1000)
```

- `nearby_infrastructure` counts real seeded roads/schools/buildings within ~2km (deliberately excludes hospitals, which represent response *capacity*, not risk exposure).
- `population_at_risk` comes from real Census 2011 district population figures (`NER_DISTRICT_POPULATION_2011`), honestly caveated as stale for districts created after 2011.

### Risk Probability & Outlook
`GET /api/v1/outlook?district=` runs real least-squares linear regression over a district's recent prediction history to report a trend (rising/falling/stable) and, when risk is rising and below the critical threshold, an estimated number of days until it crosses 75 — capped at 365 days, not a canned message.

### Satellite vegetation-change detection
`GET /api/v1/ndvi-change?cell_id=` compares real Sentinel-2 NDVI imagery (via Sentinel Hub's Statistical + Process APIs) between a recent 90-day window and the same season one year earlier for a risk cell's footprint, cloud/shadow/snow-masked, cached for `NDVI_CACHE_DAYS`. Returns an honest "not configured" response (no faked data) if Sentinel Hub credentials aren't set.

### Evacuation route planning
`GET /api/v1/evacuation-route` — within ~5km of the hand-seeded East Khasi Hills road network, plans via a local NetworkX Dijkstra graph with a real simulated blocked-road detour; everywhere else in NER, falls back to live OSRM routing plus a real nearest-hospital lookup via Overpass. Works for any coordinate in the region, not just the pilot district.

### Alerts — SMS with satellite fallback
Multilingual (`en`/`hi`/`as`/`bn`) alerts fire automatically from field reports, predictions, or the live monitor loop when severity crosses `ALERT_SEVERITY_THRESHOLD`, with a per-district+severity cooldown. Primary channel is SMS via Textbee (sent from a real Android phone's SIM, no telecom account needed); if that doesn't confirm delivery, a satellite fallback attempts delivery via Rock7 RockBLOCK/Iridium SBD modems registered at village or relay points — for when a landslide has taken down both the cell tower and the internet.

### Live dashboard updates
`WS /ws/live` broadcasts new risk cells, predictions, and alerts to every connected dashboard instantly via WebSocket, so multiple responders watching the dashboard see the same state in real time without refreshing.

### Field reports (mobile)
The Flutter Android app lets citizens/field officers submit a geo-tagged report (district, severity, description, optional photo/video) which queues offline and syncs when connectivity returns, auto-triggering alerts on high/critical severity reports.

## Running locally

Requires Docker Desktop, Node.js, Python 3.12+, and Flutter (for the mobile app).

```bash
cp .env.example .env      # fill in values as needed; blank is fine for a demo
docker compose up --build
```

- API: http://localhost:8000/docs

Then, in a separate terminal:
```bash
cd frontend
npm install
npm run dev
```
Dashboard: http://localhost:5173

Run backend tests:
```bash
cd backend
$env:DATABASE_URL = "postgresql+psycopg://test:test@localhost:5432/test"  # PowerShell
pytest -q
```

Build the mobile app:
```bash
cd mobile
flutter pub get
flutter build apk --release
```

## Key environment variables

See `.env.example` for the full list with defaults. Notable ones:

| Variable | Purpose |
|---|---|
| `WEATHER_PROVIDER` | `open_meteo` (real, free, keyless) or `simulated` (random walk, for offline testing) |
| `MONITOR_ENABLED` / `MONITOR_INTERVAL_SECONDS` | Controls the live risk-rescoring loop |
| `ALERT_SEVERITY_THRESHOLD` / `ALERT_COOLDOWN_MINUTES` | When alerts fire and how often |
| `ALERT_LANGUAGES` | Comma list from `en,hi,as,bn` |
| `TEXTBEE_API_KEY` / `TEXTBEE_DEVICE_ID` / `ALERT_SMS_RECIPIENTS` | Leave blank for simulated alerts; fill in for real SMS dispatch via an Android phone's SIM |
| `ROCKBLOCK_IMEI` / `ROCKBLOCK_USERNAME` / `ROCKBLOCK_PASSWORD` | Optional satellite (Iridium SBD) alert fallback for total connectivity loss |
| `SENTINELHUB_CLIENT_ID` / `SENTINELHUB_CLIENT_SECRET` / `NDVI_CACHE_DAYS` | Optional real Sentinel-2 NDVI vegetation-change detection; unset returns an honest "not configured" response |
| `API_KEY` | Optional `X-API-Key` guard on write endpoints; blank disables auth |
| `CORS_ORIGINS` | Comma list of allowed frontend origins |

## API overview

Full interactive docs at `/docs`. Key endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/predict` | Run the ML model on sensor readings; optionally writes a live cell to the map (`latitude`/`longitude`) or updates a district's baseline cell (`district`), logging a forecast point / triggering an alert |
| `POST /api/v1/reports` | Citizen/field-officer hazard report; auto-alerts on high/critical severity |
| `POST /api/v1/uploads` | Photo/video upload for a report (JPEG/PNG/WebP/MP4/WebM) |
| `GET /api/v1/risk-cells` | GeoJSON risk heatmap |
| `GET /api/v1/infrastructure` | GeoJSON hospitals/schools/buildings/roads |
| `GET /api/v1/road-status` | Road connectivity counts by status |
| `GET /api/v1/districts` | All 130 NER districts across 8 states |
| `GET /api/v1/forecast?district=` | Recent modeled risk trend for a district |
| `GET /api/v1/outlook?district=` | Regression-based trend + estimated days-to-critical |
| `GET /api/v1/priorities` | Risk cells ranked by score × real infrastructure × real population |
| `GET /api/v1/ndvi-change?cell_id=` | Real Sentinel-2 NDVI before/after vegetation-change comparison |
| `GET /api/v1/evacuation-route` | Route + nearest hospital for any NER location |
| `GET /api/v1/alerts` | Alert history (SMS + satellite delivery status) |
| `WS /ws/live` | Real-time push of new risk cells, predictions, and alerts |

## Data & ML model

Trained on a WSN (wireless sensor network) dataset (`ml/data.csv`, ~9,864 rows). Correlation analysis shows the label is driven almost entirely by 4 features (`Rainfall_mm`, `Slope_Angle`, `Soil_Saturation`, `Vegetation_Cover`); other significant features include historical landslide count and 29 different sensor fields . Validation ROC-AUC ≈ 0.974 / PR-AUC ≈ 0.970. See `risk_model_metadata.json` for full training metrics and feature importances.


## Mobile app

Flutter Android app (`mobile/`) for field officers and citizens:
- Submit geo-tagged hazard reports with an optional photo/video, queued offline and synced when connectivity returns.
- Browse recent alerts.
- District picker covering all NER states/districts.
- Localized UI strings (English/Hindi/Assamese/Bengali).





