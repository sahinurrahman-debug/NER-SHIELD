# NER-SHIELD

AI-based early warning and landslide risk monitoring platform for the North Eastern Region (NER) of India — built for Smart India Hackathon problem statement **26001**.

> **Demo / decision-support system.** Trained on a synthetic sensor dataset and seeded with one pilot district (East Khasi Hills, Meghalaya). Not an official warning system — see [Limitations](#known-limitations) before any real-world use.

**Live deployment:**
- Dashboard: https://ner-shield-dashboard.onrender.com
- API + docs: https://ner-shield-api.onrender.com/docs

---

## What it does

- Predicts landslide risk from rainfall, slope, soil saturation, vegetation cover, and 30 other sensor inputs using a trained XGBoost model.
- Pulls **real, live weather data** (rainfall + soil moisture via Open-Meteo, free/keyless) on a timer and rescoring risk automatically — no manual input needed.
- Renders a live GIS dashboard: risk heatmap, road/village/hospital infrastructure layer, road-connectivity status, weather-linked risk trend lookup, and an emergency-response prioritisation ranking.
- Lets citizens/field officers submit geo-tagged photo/video reports of cracks, slope movement, or blocked roads from a mobile app, with offline queuing.
- Fires multilingual (English/Hindi/Assamese/Bengali) SMS-style alerts automatically when risk crosses a threshold, from field reports, predictions, or the live monitoring loop — with cooldown to avoid spam.

## Architecture

```
Open-Meteo (live weather)        Citizen / field officer (mobile app)
        |                                  |
        v                                  v
              FastAPI backend (Python)
              - PostGIS (risk cells, infra, reports, alerts, prediction log)
              - Trained ML model (risk_model.joblib) + rule-based fallback
              - Alert pipeline (multilingual, SMS via Twilio or simulated)
              - Background monitor loop (rescoring every 120s)
                          |
                          v
              React dashboard (GIS map, alerts, dashboards)
```

| Piece | Stack | Deployed as |
|---|---|---|
| `backend/` | FastAPI, SQLAlchemy, GeoAlchemy2, PostGIS, XGBoost/scikit-learn | Render Web Service (Docker) |
| `frontend/` | React, TypeScript, Vite, Leaflet, PWA | Render Static Site |
| `mobile/` | Flutter (Android) | Sideloaded release APK |
| `ml/` | Training notebook (Colab) | — |

## Repo layout

```
ner-shield/
  backend/       FastAPI + PostGIS API, ML inference, alert pipeline
  frontend/      React GIS dashboard
  mobile/        Flutter field-reporting + alerts app
  ml/            Training data + Colab notebook
  infra/         Local PostGIS init SQL
  compose.yml    Local dev stack (API + Postgres)
```

## Running locally

Requires Docker Desktop, Node.js, Python 3.12+, and Flutter (for the mobile app).

```bash
cp .env.example .env      # fill in values as needed; blank is fine for a demo
docker compose up --build
```

- API: http://localhost:8000/docs
- Then, in a separate terminal:
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

## Key environment variables

See `.env.example` for the full list with defaults. Notable ones:

| Variable | Purpose |
|---|---|
| `WEATHER_PROVIDER` | `open_meteo` (real, free, keyless) or `simulated` (random walk, for offline testing) |
| `MONITOR_ENABLED` / `MONITOR_INTERVAL_SECONDS` | Controls the live risk-rescoring loop |
| `ALERT_SEVERITY_THRESHOLD` / `ALERT_COOLDOWN_MINUTES` | When alerts fire and how often |
| `ALERT_LANGUAGES` | Comma list from `en,hi,as,bn` |
| `TWILIO_*` / `ALERT_SMS_RECIPIENTS` | Leave blank for simulated alerts; fill in for real SMS dispatch |
| `API_KEY` | Optional `X-API-Key` guard on write endpoints; blank disables auth |

## API overview

Full interactive docs at `/docs`. Key endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/predict` | Run the ML model on 34 sensor readings; optionally writes a live cell to the map (`latitude`/`longitude`) and logs a forecast point / alert (`district`) |
| `POST /api/v1/reports` | Citizen/field-officer hazard report; auto-alerts on high/critical severity |
| `POST /api/v1/uploads` | Photo/video upload for a report (JPEG/PNG/WebP/MP4/WebM) |
| `GET /api/v1/risk-cells` | GeoJSON risk heatmap |
| `GET /api/v1/infrastructure` | GeoJSON roads/villages/hospitals |
| `GET /api/v1/road-status` | Road connectivity counts by status |
| `GET /api/v1/forecast?district=` | Recent modeled risk trend for a district |
| `GET /api/v1/priorities` | Risk cells ranked by score × nearby infrastructure |
| `GET /api/v1/alerts` | Alert history |

## Known limitations

- **Single pilot district** — only East Khasi Hills, Meghalaya has seeded data; architecture supports more, not yet populated.
- **No true IMD-branded feed or satellite imagery** — live weather comes from Open-Meteo (real data, not IMD); NDVI is a static input field, not a live imagery pipeline.
- **Alerts run in simulated mode by default** — the pipeline is fully real and automatic, but no phone numbers/Twilio account are wired in; add `ALERT_SMS_RECIPIENTS` + `TWILIO_*` to send real SMS.
- **Uploaded media isn't durable on Render** — the free-tier filesystem is ephemeral; a redeploy clears `/data/uploads`. Needs object storage (S3/R2/B2) before relying on it.
- **No auth/RBAC** — an optional flat `API_KEY` exists; no per-user roles or login system.
- **No Alembic migrations** — schema is auto-created (`Base.metadata.create_all()`); fine for a pilot, not for production schema changes.

## Model notes

Trained on a synthetic WSN (wireless sensor network) dataset (`ml/data.csv`). Correlation analysis shows the label is driven almost entirely by 4 features (`Rainfall_mm`, `Slope_Angle`, `Soil_Saturation`, `Vegetation_Cover`); the other 30 fields carry little independent signal in this dataset but are included for realistic multi-sensor input shape. Expect very high validation accuracy (~0.95+ ROC-AUC) — that reflects the synthetic label design, not real-world landslide predictability. See `risk_model_metadata.json` for training metrics.
