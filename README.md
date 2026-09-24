# NER-SHIELD

**AI Landslide Early Warning System That Still Alerts When the Network Goes Down** 

**Live deployment:**
- Dashboard: https://ner-shield-dashboard.onrender.com
- API + interactive docs: https://ner-shield-api.onrender.com/docs

---

## Table of contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Repo layout](#repo-layout)
- [Feature walkthrough](#feature-walkthrough)
- [Full technology stack](#full-technology-stack)
- [External real-data sources](#external-real-data-sources)
- [Database schema](#database-schema)
- [Running locally](#running-locally)
- [Key environment variables](#key-environment-variables)
- [Full API reference](#full-api-reference)
- [ML model](#ml-model)
- [Mobile app](#mobile-app)
- [Resilience & reliability engineering](#resilience--reliability-engineering)
- [Testing](#testing)
- [Deployment](#deployment)


## What it does

- **Predicts landslide risk** from rainfall, slope, soil saturation, vegetation cover and 30 other sensor-style inputs using a trained XGBoost model (calibrated, with a rule-based fallback if the model can't load), including a SHAP-based explainability breakdown of which factors drove a given prediction.
- **Covers all 8 NER states / 130 districts** — every district is geocoded and seeded with a baseline risk cell on first startup, so a prediction for *any* NER district immediately shows up on the map, in the emergency table, and in the outlook panel.
- **Pulls real, live weather data** (rainfall + soil moisture via Open-Meteo, free/keyless) on a timer and automatically rescores risk; the same live weather also auto-fills any `/predict` call for any district that omits it.
- **Real terrain everywhere**: elevation/slope/aspect for every risk cell computed from NASA/USGS SRTM 30m DEM data, not hand-typed placeholders.
- **Real historical landslide records**: NASA's COOLR catalog, queried per-state across all 8 NER states, feeding a real `Historical_Landslide_Count` into predictions.
- **Renders a live GIS dashboard**: risk heatmap, real infrastructure layer (hospitals, schools, key public buildings, major roads — seeded NER-wide from OpenStreetMap), road-connectivity status, weather-linked risk trend + statistical outlook (real least-squares regression, not a canned message), satellite NDVI vegetation-change detection (real Sentinel-2 imagery), and an emergency-response prioritisation ranking backed by real Census 2011 population data.
- **Pushes updates live** over WebSocket (`/ws/live`) — new risk cells, predictions, and alerts appear on every connected dashboard instantly, no polling.
- **Plans evacuation routes** for any location in NER: a real local road-network graph (Dijkstra, with simulated blocked-road detours) falling back to live OSRM routing + nearest real hospital lookup (via OpenStreetMap Overpass) everywhere else in the region.
- **Lets citizens/field officers submit geo-tagged photo/video reports** of cracks, slope movement, or blocked roads from a mobile app, with offline queuing and a basic trust/spam score.
- **Fires multilingual (English/Hindi/Assamese/Bengali) alerts** automatically when risk crosses a threshold — from field reports, predictions, or the live monitoring loop — via SMS (Textbee, using an Android phone's own SIM) with cooldown to avoid spam, and a **satellite fallback** (Rock7 RockBLOCK / Iridium SBD) for when a landslide has taken down both the local cell tower and the internet.
- **Works offline**: the web dashboard is a real PWA (service worker, app-shell caching); the mobile app queues field reports locally and syncs when connectivity returns.

## Architecture

```
Open-Meteo        OpenStreetMap Overpass     Sentinel Hub      OSRM        Open Topo Data      NASA COOLR
(rainfall+soil)   (hospitals/schools/roads)  (NDVI imagery)    (routing)   (SRTM 30m DEM)       (landslide events)
      |                    |                       |              |              |                    |
      v                    v                       v              v              v                    v
                              FastAPI backend (Python 3.12, Docker on Render)
                              - PostGIS: risk_cells, infrastructure, field_reports, alerts,
                                prediction_log, ndvi_readings, historical_landslides
                              - Trained XGBoost model (risk_model.joblib) + rule-based
                                fallback + SHAP explainability (TreeExplainer)
                              - Background monitor loop (live rescoring every 120s)
                              - 5 one-time background seed tasks (hospitals, schools/roads/
                                buildings, district baselines, terrain, historical landslides)
                              - Real-defaults enrichment at /predict time (terrain, landslide
                                history, live weather) for any district/location
                              - Alert pipeline: multilingual SMS (Textbee) -> satellite
                                fallback (RockBLOCK/Iridium SBD)
                              - WebSocket broadcast (/ws/live) for real-time dashboard updates
                              - NetworkX Dijkstra local road graph (evacuation routing)
                                          |                                  |
                                          v                                  v
                    React + Leaflet dashboard (Render Static Site, PWA)   Flutter mobile app (Android)
                    - GIS map, alerts, prioritisation, outlook            - Field reports (photo/video,
                    - Evacuation planner, NDVI panel, SHAP sandbox          offline queue via shared_preferences)
                    - Offline app-shell caching (vite-plugin-pwa)          - Alerts feed, district picker
                                                                           - Localized UI (en/hi/as/bn)
```

| Piece | Stack | Deployed as |
|---|---|---|
| `backend/` | FastAPI, SQLAlchemy 2.x, GeoAlchemy2, PostGIS, XGBoost/scikit-learn, SHAP, NetworkX, httpx | Render Web Service (Docker) |
| `frontend/` | React 19, TypeScript, Vite, Leaflet/react-leaflet, vite-plugin-pwa | Render Static Site |
| `mobile/` | Flutter/Dart (Android) | Sideloaded release APK |
| `ml/` | Training notebook + dataset (Colab) | — |

## Repo layout

```
ner-shield/
  backend/       FastAPI + PostGIS API, ML inference, alert pipeline, seed/background tasks
    app/main.py    Single-module backend: models, endpoints, background tasks, ML inference
    models/        Trained model artifact (risk_model.joblib)
    tests/         pytest suite
  frontend/      React GIS dashboard (Vite, TypeScript, Leaflet)
    src/App.tsx    Main dashboard component (map, panels, forms)
    src/api.ts     Typed fetch client for the backend API
  mobile/        Flutter field-reporting + alerts app (Android)
    lib/main.dart      App entry, screens, offline queue
    lib/districts.dart NER district/state data for the picker
    lib/strings.dart   Localized UI strings (en/hi/as/bn)
  ml/            Training data + Colab notebook
  infra/         Local PostGIS init SQL
  compose.yml    Local dev stack (API + Postgres/PostGIS)
  risk_model_metadata.json   Training metrics, feature order, feature importances
```

## Feature walkthrough

### Risk prediction & explainability
`POST /api/v1/predict` runs the trained model on up to 34 sensor-style features. Called with `latitude`/`longitude` it places/updates a live risk cell on the map; called with `district` it updates that district's baseline cell directly (auto-geocoding a first-time cell if needed) — so **every prediction for every NER district shows up in the Emergency Response Prioritisation table**, not just the pilot area. Before the model runs, `_apply_real_defaults()` fills in any *omitted* field with real cached data — real SRTM slope/elevation/aspect, real NASA COOLR landslide counts, and real live Open-Meteo rainfall/soil-moisture — for the resolved district/location, without ever overriding a value the caller actually supplied. A separate explainability sandbox on the dashboard runs SHAP against the same model without writing to the map, so it's safe to experiment with.

### NER-wide district coverage
`GET /api/v1/districts` serves all 130 districts across the 8 NER states (`NER_DISTRICTS` in `backend/app/main.py`). A background task (`seed_district_baseline_cells`) geocodes and seeds a neutral baseline cell for every district on first startup (rate-limited to Nominatim's 1 req/sec policy, with a Photon fallback), so no district is ever "missing" from the map.

### Real infrastructure, NER-wide
Two background seed tasks populate the `infrastructure` table from live OpenStreetMap data across the full NER bounding box:
- **Hospitals** (`seed_ner_hospitals`) — ~3,000 real hospitals, used by the evacuation planner's nearest-hospital lookup.
- **Schools, key public buildings, and major roads** (`seed_ner_infrastructure`) — real schools, colleges/town halls/community centres/marketplaces, and motorway/trunk/primary roads, fetched as lightweight center-points (not full road geometry) with a hard result cap and chunked commits, specifically designed to stay within Render's free-tier 512MB RAM limit (see [Resilience & reliability engineering](#resilience--reliability-engineering)).

Both seeds are idempotent (skip once populated), run as background tasks so they never block startup, and degrade gracefully (retry next restart) if OpenStreetMap's Overpass API is unreachable — with automatic fallback across three independent Overpass mirrors.

### Real terrain, NER-wide
`seed_ner_terrain()` computes real elevation/slope/aspect for every risk cell (all 130 district baselines plus the demo cells) from NASA/USGS SRTM 30m DEM data via the free Open Topo Data API. Since the API only returns raw elevation, slope/aspect are derived with the standard GIS finite-difference method: sample elevation at the target point plus its N/S/E/W neighbours ~100m away, then compute the terrain gradient across those five samples. Runs after district baseline seeding (via a small sequencing coroutine, `seed_district_baselines_then_terrain`), paced at ~1 request/second.

### Real historical landslide records, NER-wide
`seed_historical_landslides()` pulls real historical landslide event records from NASA's COOLR (Cooperative Open Online Landslide Repository) — a public ArcGIS Feature Service. Queries per NER state (8 overlapping bounding boxes, deduplicated by `event_id`) rather than one region-wide query, since a single query only returns the first ~2000 records the server happens to enumerate — which in practice were almost entirely one dense Mizoram/Bangladesh-border batch. Each event is assigned to its nearest NER district within a 70km cutoff (events further away are left unlabeled rather than mislabeled to a distant district). 

### Emergency Response Prioritisation
`GET /api/v1/priorities` ranks every risk cell by:

```
priority_score = risk_score * (1 + 0.2 * nearby_infrastructure + 0.05 * population_at_risk / 1000)
```

- `nearby_infrastructure` counts real seeded roads/schools/buildings within ~2km (deliberately excludes hospitals, which represent response *capacity*, not risk exposure).
- `population_at_risk` comes from real Census 2011 district population figures (`NER_DISTRICT_POPULATION_2011`), honestly caveated as stale for districts created after 2011.

### Risk Probability & Outlook
`GET /api/v1/outlook?district=` runs real least-squares linear regression over a district's recent prediction history (up to 10 `PredictionLog` rows) to report a trend (`trend_per_day`) and, when risk is rising and below the critical threshold, an estimated number of days until it crosses 75 — capped at 365 days, not a canned message.

### Satellite vegetation-change detection
`GET /api/v1/ndvi-change?cell_id=` compares real Sentinel-2 NDVI imagery (via Sentinel Hub's Statistical + Process APIs) between a recent 90-day window and the same season one year earlier for a risk cell's footprint, cloud/shadow/snow-masked via the scene classification band, cached for `NDVI_CACHE_DAYS`. Returns an honest "not configured" response (no faked data) if Sentinel Hub credentials aren't set, and an honest "unavailable" numeric result (while still returning the real before/after images) if no cloud-free pixels exist in either window — common in NER's monsoon climate.

### Evacuation route planning
`GET /api/v1/evacuation-route` — within ~5km of the hand-seeded East Khasi Hills road network, plans via a local NetworkX Dijkstra graph with a real simulated blocked-road detour; everywhere else in NER, falls back to live OSRM routing plus a real nearest-hospital lookup via Overpass. Works for any coordinate in the region, not just the pilot district.

### Alerts — SMS with satellite fallback
Multilingual (`en`/`hi`/`as`/`bn`) alerts fire automatically from field reports, predictions, or the live monitor loop when severity crosses `ALERT_SEVERITY_THRESHOLD`, with a per-district+severity cooldown. Primary channel is SMS via Textbee (sent from a real Android phone's SIM, no telecom account needed); if that doesn't confirm delivery, a satellite fallback attempts delivery via Rock7 RockBLOCK/Iridium SBD modems registered at village or relay points — for when a landslide has taken down both the cell tower and the internet.

### Live dashboard updates
`WS /ws/live` broadcasts new risk cells, predictions, and alerts to every connected dashboard instantly via WebSocket, so multiple responders watching the dashboard see the same state in real time without refreshing.

### Field reports (mobile)
The Flutter Android app lets citizens/field officers submit a geo-tagged report (district, severity, description, optional photo/video) which queues offline (via `shared_preferences`) and syncs when connectivity returns, auto-triggering alerts on high/critical severity reports.

## Full technology stack

### Backend (`backend/requirements.txt`)
| Package | Version | Used for |
|---|---|---|
| `fastapi[standard]` | 0.115.x | Web framework, request validation (Pydantic), OpenAPI docs |
| `uvicorn[standard]` | 0.34.0 | ASGI server |
| `sqlalchemy` | 2.x | ORM (declarative `Mapped`/`mapped_column` style) |
| `geoalchemy2` | 0.15.x | PostGIS geometry column types in SQLAlchemy |
| `psycopg[binary]` | 3.2.x | PostgreSQL driver |
| `numpy` | 1.26–2.x | Numeric arrays for model input/output |
| `joblib` | 1.4.x | Loading the trained model artifact |
| `scikit-learn` | 1.6.1 | `CalibratedClassifierCV`, `SimpleImputer` pipeline |
| `xgboost` | 3.4.1 | The trained gradient-boosted tree classifier |
| `shap` | 0.52.0 | `TreeExplainer` for per-prediction feature attribution |
| `networkx` | 3.2.x | Dijkstra shortest-path over the local road graph |
| `Pillow` | 10–12.x | Image validation/processing for report uploads |
| `python-multipart` | 0.0.9.x | Multipart form parsing for file uploads |
| `httpx` | 0.27.x | All outbound HTTP calls (every external integration below) |
| `pytest` | 8.x | Test runner |

Plus: Python's own `asyncio` (background tasks, monitor loop), `socket` (IPv4-forcing monkey-patch — see [Resilience](#resilience--reliability-engineering)), `contextlib.asynccontextmanager` (FastAPI lifespan), `json`, `logging`, `math` (haversine, terrain gradient trig), `random` (simulated-weather fallback), `uuid`.

### Frontend (`frontend/package.json`)
| Package | Version | Used for |
|---|---|---|
| `react` / `react-dom` | 19.2.x | UI framework |
| `leaflet` / `react-leaflet` | 1.9.x / 5.0.x | Interactive GIS map |
| `vite` | 8.2.x | Build tool / dev server |
| `vite-plugin-pwa` | 1.3.x | Service worker generation, offline app-shell caching, install manifest |
| `typescript` | 6.0.x | Type safety across `App.tsx`/`api.ts` |
| `eslint` + `typescript-eslint` | — | Linting |

### Mobile (`mobile/pubspec.yaml`)
| Package | Version | Used for |
|---|---|---|
| `http` | 1.6.x | API calls to the backend |
| `shared_preferences` | 2.5.x | Local offline queue for field reports |
| `geolocator` | 14.0.x | Geo-tagging reports with the device's real location |
| `image_picker` | 1.2.x | Attaching a photo/video to a report |
| `http_parser` | 4.1.x | Multipart upload construction |
| Flutter SDK | ^3.13.1 | Cross-platform app framework (Android target) |

### Infrastructure
- **Docker** (`backend/Dockerfile`) — `python:3.12-slim` base, installs `requirements.txt`, runs `uvicorn app.main:app`.
- **Render** — backend as a Web Service (Docker), frontend as a Static Site, managed PostgreSQL with the PostGIS extension.
- **`compose.yml`** — local dev stack (API container + Postgres/PostGIS container).
- **GitHub Actions** (`.github/workflows/ci.yml`) — `backend-tests` job (`pytest -q` against a Postgres service container) and `frontend-build` job (`npm ci && npm run build`) on every push/PR to `main`.

## External real-data sources

Every one of these is a genuinely live/real integration — no synthetic stand-ins for any of them. All are free/keyless except Textbee, RockBLOCK, and Sentinel Hub, which need a (free-tier) account.

| Source | Used for | Auth | Notes |
|---|---|---|---|
| **Open-Meteo** (`api.open-meteo.com`) | Live rainfall + soil moisture | None | Free, keyless global weather API |
| **OpenStreetMap Overpass** (3 mirrors: `overpass-api.de`, `overpass.openstreetmap.fr`, `maps.mail.ru`) | Real hospitals, schools, buildings, major roads, nearest-hospital lookup | None | Tries each mirror in turn; whichever responds first wins |
| **Nominatim** (`nominatim.openstreetmap.org`) + **Photon** (`photon.komoot.io`) | Geocoding a district's approximate town centre | None | Nominatim primary, Photon fallback, paced at 1 req/sec |
| **Open Topo Data** (`api.opentopodata.org`) | Real elevation from NASA/USGS SRTM 30m DEM | None | Free public demo server; slope/aspect computed locally from 5 sampled points |
| **NASA COOLR** (`gis.earthdata.nasa.gov`, ArcGIS Feature Service) | Real historical landslide event records | None | Public feature service; queried per NER state |
| **OSRM** (`router.project-osrm.org`) | Turn-by-turn routing outside the local pilot road network | None | Public demo routing server |
| **Sentinel Hub** (Statistical + Process APIs) | Real Sentinel-2 NDVI vegetation-change imagery | OAuth client ID/secret | Free trial tier; `SENTINELHUB_CLIENT_ID`/`SENTINELHUB_CLIENT_SECRET` |
| **Textbee** (`textbee.dev`) | Real SMS dispatch via an Android phone's own SIM | API key | Free tier; `TEXTBEE_API_KEY`/`TEXTBEE_DEVICE_ID` |
| **Rock7 RockBLOCK / Iridium SBD** | Satellite alert fallback when SMS/internet both fail | Account credentials | `ROCKBLOCK_IMEI`/`ROCKBLOCK_USERNAME`/`ROCKBLOCK_PASSWORD` |

## Database schema

PostgreSQL + PostGIS. Tables (all in `backend/app/main.py` as SQLAlchemy models):

| Table | Purpose | Key columns |
|---|---|---|
| `risk_cells` | Every map risk cell — seeded districts, demo cells, live `/predict` writes | `cell_id`, `district`, `geom` (POLYGON), `slope_deg`, `elevation_m`, `aspect_deg`, `rain_24h_mm`, `soil_moisture_pct`, `historical_density`, `risk_score`, `severity` |
| `infrastructure` | Real hospitals/schools/buildings/roads/villages | `kind`, `name`, `district`, `status`, `population`, `geom` (POINT/LINESTRING) |
| `field_reports` | Citizen/field-officer hazard reports | `district`, `report_type`, `severity`, `description`, `latitude`/`longitude`, `trust_score`, `trust_flags` |
| `alerts` | Alert history | `channel`, `recipients`, `status`, `satellite_status`, `satellite_note` |
| `prediction_log` | Every `/predict` call's score, for the outlook regression | `district`, `risk_score`, `severity`, `rain_24h_mm`, `source` |
| `ndvi_readings` | Cached Sentinel-2 NDVI results per cell | `cell_id`, `mean_ndvi_before/after`, `ndvi_delta`, `vegetation_loss_pct`, image URLs |
| `historical_landslides` | Real NASA COOLR event records | `event_id`, `title`, `event_date`, `category`, `landslide_trigger`, `source_name`, `citation`, `district`, `geom` (POINT) |

All spatial columns are indexed (`spatial_index=True`); most queries use `ST_DWithin`, `ST_Distance`, or `ST_Centroid`.

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

See `.env.example` for the full list with defaults.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (PostGIS extension required) |
| `MODEL_PATH` | Path to the trained model artifact (default `/app/models/risk_model.joblib`) |
| `UPLOAD_DIR` / `MAX_UPLOAD_MB` | Where field-report photos/videos are stored, and the size cap |
| `CORS_ORIGINS` | Comma list of allowed frontend origins |
| `WEATHER_PROVIDER` | `open_meteo` (real, free, keyless) or `simulated` (random walk, for offline testing) |
| `MONITOR_ENABLED` / `MONITOR_INTERVAL_SECONDS` | Controls the live risk-rescoring loop (default 120s) |
| `ALERT_SEVERITY_THRESHOLD` / `ALERT_COOLDOWN_MINUTES` | When alerts fire and how often |
| `ALERT_LANGUAGES` | Comma list from `en,hi,as,bn` |
| `TEXTBEE_API_KEY` / `TEXTBEE_DEVICE_ID` / `ALERT_SMS_RECIPIENTS` | Leave blank for simulated alerts; fill in for real SMS dispatch via an Android phone's SIM |
| `ROCKBLOCK_IMEI` / `ROCKBLOCK_USERNAME` / `ROCKBLOCK_PASSWORD` | Optional satellite (Iridium SBD) alert fallback for total connectivity loss |
| `SENTINELHUB_CLIENT_ID` / `SENTINELHUB_CLIENT_SECRET` / `NDVI_CACHE_DAYS` | Optional real Sentinel-2 NDVI vegetation-change detection; unset returns an honest "not configured" response |
| `API_KEY` | Optional `X-API-Key` guard on write endpoints; blank disables auth |

## Full API reference

Interactive docs (Swagger UI) at `/docs`.

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness/readiness check |
| `GET /api/v1/summary` | Aggregate counts for the dashboard header |
| `GET /api/v1/risk-cells?min_score=` | GeoJSON risk heatmap (includes real `slope_deg`/`elevation_m`/`aspect_deg`) |
| `WS /ws/live` | Real-time push of new risk cells, predictions, and alerts |
| `GET /api/v1/infrastructure` | GeoJSON hospitals/schools/buildings/roads |
| `GET /api/v1/historical-landslides` | GeoJSON real NASA COOLR landslide event records |
| `GET /api/v1/road-status` | Road connectivity counts by status |
| `GET /api/v1/districts` | All 130 NER districts across 8 states |
| `GET /api/v1/forecast?district=` | Recent modeled risk trend for a district |
| `GET /api/v1/priorities` | Risk cells ranked by score × real infrastructure × real population |
| `GET /api/v1/ndvi-change?cell_id=&force_refresh=` | Real Sentinel-2 NDVI before/after vegetation-change comparison |
| `GET /api/v1/evacuation-route?...` | Route + nearest hospital for any NER location |
| `GET /api/v1/reports` | Field report history |
| `GET /api/v1/alerts` | Alert history (SMS + satellite delivery status) |
| `POST /api/v1/uploads` | Photo/video upload for a report (JPEG/PNG/WebP/MP4/WebM) — requires `X-API-Key` if `API_KEY` is set |
| `POST /api/v1/reports` | Citizen/field-officer hazard report; auto-alerts on high/critical severity — requires `X-API-Key` if set |
| `POST /api/v1/predict` | Run the ML model on sensor readings, enriched with real terrain/history/weather; writes a live cell / updates a district baseline; logs a forecast point; may trigger an alert — requires `X-API-Key` if set |
| `GET /api/v1/outlook?district=` | Regression-based trend + estimated days-to-critical |

## ML model

Trained on a WSN (wireless sensor network) dataset (`ml/data.csv`, ~9,864 rows) in a Colab notebook. XGBoost classifier wrapped in `CalibratedClassifierCV` (probability calibration) with a `SimpleImputer` (median strategy) for any of the 34 features a caller omits. Correlation analysis shows the label is driven almost entirely by 4 features (`Rainfall_mm`, `Slope_Angle`, `Soil_Saturation`, `Vegetation_Cover`); `Historical_Landslide_Count` and the remaining sensor fields carry secondary signal. Validation **ROC-AUC ≈ 0.974**, **PR-AUC ≈ 0.970** (see `risk_model_metadata.json` for full metrics, feature order, and feature importances). SHAP's `TreeExplainer` runs against the model's underlying trees for per-prediction attribution, exposed via the `explanation` field in `/predict`'s response.

Terrain, historical-landslide-count, and rainfall/soil-moisture inputs are real live/derived data (see [External real-data sources](#external-real-data-sources)) — only the core trained model itself learned from the synthetic WSN dataset, clearly separated so it's obvious which parts of a prediction are real-world-grounded and which are demo/pilot.

## Mobile app

Flutter Android app (`mobile/`) for field officers and citizens:
- Submit geo-tagged hazard reports (via `geolocator`) with an optional photo/video (via `image_picker`), queued offline in `shared_preferences` and synced when connectivity returns.
- Browse recent alerts.
- District picker covering all NER states/districts (`lib/districts.dart`).
- Localized UI strings — English/Hindi/Assamese/Bengali (`lib/strings.dart`).

Built as a release APK (`flutter build apk --release`); not published to the Play Store.

## Resilience & reliability engineering

A few non-obvious things this backend does to stay stable on a resource-constrained free-tier host:

- **IPv6-avoidance monkey-patch**: right after imports in `main.py`, `socket.getaddrinfo` is globally patched to force `AF_INET` only. Render's container has no IPv6 route, and several outbound hosts (Overpass, Sentinel Hub, etc.) publish AAAA records that would otherwise be tried first and hang.
- **Multi-mirror Overpass fallback**: `_overpass_post()` tries three independent Overpass mirrors in turn, so one mirror's downtime or rate-limiting doesn't take down hospital/school/road seeding.
- **Memory-safe seeding**: an earlier version of the infrastructure seed requested full road geometry (`out center geom;`) for every major road in NER, which held enough parsed coordinate data in memory at once to exceed Render's 512MB limit and crash the backend into a restart loop. Fixed by requesting only center points (`out center N;`) with a hard numeric result cap, and committing in bounded chunks (500 rows at a time) instead of one giant batch — the same pattern used for hospitals, historical landslides, and infrastructure.
- **`MONITORED_CELL_IDS` allowlist**: the live monitor loop only continuously rescoring a small, explicit set of showcase cells — not every risk cell — so it can't silently overwrite a district's real `/predict` reading.
- **Every background seed task is wrapped in its own try/except with staged logging**: since they run as fire-and-forget `asyncio.create_task()`s in `lifespan()`, an unhandled exception would otherwise die completely silently.

## Testing

- **Backend**: `pytest` (`backend/tests/`), run in CI against a real Postgres service container.
- **Frontend**: `npm run build` (TypeScript compile + Vite build) in CI, catching type errors before deploy.
- **Manual verification discipline** used throughout development: every real-data integration (Overpass, Open-Meteo, Sentinel Hub, Open Topo Data, NASA COOLR, OSRM) was hand-tested live against its real endpoint before being wired into the app, and re-verified live against the production deployment after each deploy.

## Deployment

- **Backend**: Render Web Service, built from `backend/Dockerfile`, connected to a Render-managed PostgreSQL instance with PostGIS enabled.
- **Frontend**: Render Static Site, built via `npm run build`.
- **CI**: GitHub Actions (`.github/workflows/ci.yml`) runs backend pytest + frontend build on every push/PR to `main`.
- Database schema changes are applied idempotently at every startup via `Base.metadata.create_all()` (new tables) plus explicit `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements in `lifespan()` (new columns on existing tables) — no Alembic migrations.


