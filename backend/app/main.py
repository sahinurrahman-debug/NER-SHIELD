import asyncio
import json
import logging
import os
import random
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
import joblib
import numpy as np
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from geoalchemy2 import Geometry
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]
MODEL_PATH = os.getenv("MODEL_PATH", "/app/models/risk_model.joblib")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "8"))
CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")]
Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

# Optional: unset means auth is disabled (fine for local/demo use). Set it to require
# an X-API-Key header on the write endpoints below before any real deployment.
API_KEY = os.getenv("API_KEY")

TEXTBEE_API_KEY = os.getenv("TEXTBEE_API_KEY")
TEXTBEE_DEVICE_ID = os.getenv("TEXTBEE_DEVICE_ID")  # optional; blank uses the account's default device
ALERT_SMS_RECIPIENTS = [x.strip() for x in os.getenv("ALERT_SMS_RECIPIENTS", "").split(",") if x.strip()]
ALERT_SEVERITY_THRESHOLD = os.getenv("ALERT_SEVERITY_THRESHOLD", "high")
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "15"))
ALERT_LANGUAGES = [x.strip() for x in os.getenv("ALERT_LANGUAGES", "en,hi,as,bn").split(",") if x.strip()]
SEVERITY_ORDER = ["low", "moderate", "high", "critical"]
# Representative risk_score for a severity band, used when a field report (which carries
# no raw sensor readings) needs to place/update a risk cell on the map.
SEVERITY_MIDPOINT_SCORE = {"low": 15.0, "moderate": 40.0, "high": 65.0, "critical": 90.0}

# Real-time monitoring loop (see run_monitor_tick). Pulls live rainfall/soil-moisture from
# Open-Meteo (free, keyless) per risk cell; falls back to a random walk if that call fails
# or WEATHER_PROVIDER=simulated. Swap in IMD's feed here once institutional access exists.
MONITOR_ENABLED = os.getenv("MONITOR_ENABLED", "true").lower() == "true"
MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "120"))
WEATHER_PROVIDER = os.getenv("WEATHER_PROVIDER", "open_meteo")  # "open_meteo" | "simulated"

# Must exactly match the training notebook's FEATURES list and order (see ml/notebooks).
FEATURES = [
    "Rainfall_mm", "Slope_Angle", "Soil_Saturation", "Vegetation_Cover",
    "Rainfall_3Day", "Rainfall_7Day", "Aspect", "Elevation_m", "NDVI_Index",
    "Land_Use_Urban", "Land_Use_Forest", "Land_Use_Agriculture",
    "Earthquake_Activity", "Proximity_to_Water", "Distance_to_Road_m",
    "Temperature_C", "Humidity_percent", "Soil_pH", "Clay_Content",
    "Sand_Content", "Silt_Content", "Soil_Erosion_Rate",
    "Historical_Landslide_Count", "Soil_Type_Gravel", "Soil_Type_Sand",
    "Soil_Type_Silt", "Soil_Type_Clay", "Pore_Water_Pressure_kPa",
    "Soil_Moisture_Content", "Microseismic_Activity", "Acoustic_Emission_dB",
    "Soil_Strain", "Soil_Temperature_C", "TDR_Reflection_Index",
]
Severity = Literal["low", "moderate", "high", "critical"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
loaded_model = None
monitor_task: asyncio.Task | None = None


class Base(DeclarativeBase):
    pass


class RiskCell(Base):
    __tablename__ = "risk_cells"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cell_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    district: Mapped[str] = mapped_column(String(100), index=True)
    geom: Mapped[object] = mapped_column(Geometry("POLYGON", srid=4326, spatial_index=True))
    slope_deg: Mapped[float] = mapped_column(Float)
    rain_24h_mm: Mapped[float] = mapped_column(Float, default=0)
    soil_moisture_pct: Mapped[float] = mapped_column(Float, default=0)
    historical_density: Mapped[float] = mapped_column(Float, default=0)
    risk_score: Mapped[float] = mapped_column(Float, default=0, index=True)
    severity: Mapped[str] = mapped_column(String(16), default="low", index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FieldReport(Base):
    __tablename__ = "field_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_type: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    description: Mapped[str] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(String(100), nullable=True)
    road_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # photo or video URL
    reporter_role: Mapped[str] = mapped_column(String(32), default="citizen")
    verification_status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)  # "field_report" | "prediction" | "monitor"
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    district: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    message: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(16), default="sms")
    recipients: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # "sent" | "simulated" | "failed" | "no_recipients"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Infrastructure(Base):
    __tablename__ = "infrastructure"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # "road" | "village" | "hospital" | "school"
    name: Mapped[str] = mapped_column(String(150))
    district: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # roads only
    geom: Mapped[object] = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True))


class PredictionLog(Base):
    __tablename__ = "prediction_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    district: Mapped[str] = mapped_column(String(100), index=True)
    risk_score: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(16))
    rain_24h_mm: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RiskFeatures(BaseModel):
    """All 34 sensor readings are optional — a real field station may only have a rain
    gauge, not full soil chemistry sensors. Anything omitted is imputed: the trained
    model uses its built-in median imputer (np.nan in predict()); the rule-based
    fallback and map/forecast writes use NEUTRAL_DEFAULTS. See predict() and
    fallback_probability() for how each path actually handles a missing value."""
    Rainfall_mm: float | None = Field(default=None, ge=0, le=500)
    Slope_Angle: float | None = Field(default=None, ge=0, le=90)
    Soil_Saturation: float | None = Field(default=None, ge=0, le=1)
    Vegetation_Cover: float | None = Field(default=None, ge=0, le=1)
    Rainfall_3Day: float | None = Field(default=None, ge=0, le=1000)
    Rainfall_7Day: float | None = Field(default=None, ge=0, le=1500)
    Aspect: float | None = Field(default=None, ge=0, le=360)
    Elevation_m: float | None = Field(default=None, ge=0, le=4000)
    NDVI_Index: float | None = Field(default=None, ge=-1, le=1)
    Land_Use_Urban: Literal[0, 1] | None = None
    Land_Use_Forest: Literal[0, 1] | None = None
    Land_Use_Agriculture: Literal[0, 1] | None = None
    Earthquake_Activity: float | None = Field(default=None, ge=0, le=10)
    Proximity_to_Water: float | None = Field(default=None, ge=0, le=1)
    Distance_to_Road_m: float | None = Field(default=None, ge=0, le=2000)
    Temperature_C: float | None = Field(default=None, ge=-10, le=50)
    Humidity_percent: float | None = Field(default=None, ge=0, le=100)
    Soil_pH: float | None = Field(default=None, ge=0, le=14)
    Clay_Content: float | None = Field(default=None, ge=0, le=100)
    Sand_Content: float | None = Field(default=None, ge=0, le=100)
    Silt_Content: float | None = Field(default=None, ge=0, le=100)
    Soil_Erosion_Rate: float | None = Field(default=None, ge=0, le=100)
    Historical_Landslide_Count: float | None = Field(default=None, ge=0, le=20)
    Soil_Type_Gravel: Literal[0, 1] | None = None
    Soil_Type_Sand: Literal[0, 1] | None = None
    Soil_Type_Silt: Literal[0, 1] | None = None
    Soil_Type_Clay: Literal[0, 1] | None = None
    Pore_Water_Pressure_kPa: float | None = Field(default=None, ge=0, le=300)
    Soil_Moisture_Content: float | None = Field(default=None, ge=0, le=1)
    Microseismic_Activity: float | None = Field(default=None, ge=0, le=1)
    Acoustic_Emission_dB: float | None = Field(default=None, ge=0, le=150)
    Soil_Strain: float | None = Field(default=None, ge=0, le=1)
    Soil_Temperature_C: float | None = Field(default=None, ge=-10, le=50)
    TDR_Reflection_Index: float | None = Field(default=None, ge=0, le=3)
    # Labels only, below — not model features, never passed into predict().
    district: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ReportCreate(BaseModel):
    report_type: Literal["crack", "slope_movement", "landslide", "blocked_road", "rockfall", "flooding"]
    severity: Severity
    description: str = Field(min_length=3, max_length=2000)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    district: str | None = None
    road_status: Literal["open", "restricted", "partial_block", "blocked"] | None = None
    image_url: str | None = None
    reporter_role: Literal["citizen", "field_officer"] = "citizen"


class ReportOut(ReportCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    verification_status: str
    created_at: datetime


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_type: str
    source_id: str | None
    district: str | None
    severity: str
    message: str
    channel: str
    recipients: str | None
    status: str
    created_at: datetime


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Missing or invalid X-API-Key")


def severity_for(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 30:
        return "moderate"
    return "low"


# Training-data-representative medians, used only when a field is omitted from a request —
# lets the rule-based fallback and map/forecast writes still produce a sensible number from
# a partial reading (e.g. rainfall only) instead of crashing on a missing value.
NEUTRAL_DEFAULTS = {
    "Rainfall_mm": 150.0, "Slope_Angle": 42.0, "Soil_Saturation": 0.55,
    "Vegetation_Cover": 0.5, "Rainfall_3Day": 300.0, "Historical_Landslide_Count": 2.0,
    "Soil_Moisture_Content": 0.3,
}


def _or_default(value: float | None, field: str) -> float:
    return value if value is not None else NEUTRAL_DEFAULTS[field]


def fallback_probability(p: RiskFeatures) -> float:
    """Transparent demo score built from the 4 features that actually drive risk
    in the training data (Rainfall_mm, Slope_Angle, Soil_Saturation, Vegetation_Cover);
    it is not a trained or authoritative forecast. Any omitted field falls back to a
    training-representative default rather than failing the request."""
    score = (
        min(_or_default(p.Soil_Saturation, "Soil_Saturation"), 1) * 0.30
        + min(_or_default(p.Rainfall_mm, "Rainfall_mm") / 300, 1) * 0.25
        + min(_or_default(p.Slope_Angle, "Slope_Angle") / 80, 1) * 0.20
        + (1 - min(_or_default(p.Vegetation_Cover, "Vegetation_Cover"), 1)) * 0.15
        + min(_or_default(p.Rainfall_3Day, "Rainfall_3Day") / 600, 1) * 0.05
        + min(_or_default(p.Historical_Landslide_Count, "Historical_Landslide_Count") / 6, 1) * 0.05
    )
    return float(np.clip(score, 0, 1))


def contributing_factors(p: RiskFeatures) -> list[str]:
    factors: list[str] = []
    if p.Soil_Saturation is not None and p.Soil_Saturation >= 0.7:
        factors.append("high soil saturation")
    if p.Rainfall_mm is not None and p.Rainfall_mm >= 150:
        factors.append("heavy recent rainfall")
    if p.Rainfall_3Day is not None and p.Rainfall_3Day >= 300:
        factors.append("prolonged 3-day rainfall")
    if p.Slope_Angle is not None and p.Slope_Angle >= 45:
        factors.append("steep slope")
    if p.Vegetation_Cover is not None and p.Vegetation_Cover <= 0.3:
        factors.append("sparse vegetation cover")
    if p.Historical_Landslide_Count is not None and p.Historical_Landslide_Count >= 2:
        factors.append("prior landslide history in area")
    return factors or ["no dominant trigger detected"]


def missing_features(p: RiskFeatures) -> list[str]:
    return [feature for feature in FEATURES if getattr(p, feature) is None]


def predict(p: RiskFeatures) -> tuple[float, str]:
    if loaded_model is not None:
        # NaN for anything omitted — the trained pipeline's own SimpleImputer (median,
        # learned at training time) fills it in, rather than requiring all 34 readings.
        row = np.array(
            [[getattr(p, feature) if getattr(p, feature) is not None else np.nan for feature in FEATURES]],
            dtype=float,
        )
        return float(loaded_model.predict_proba(row)[0][1]), "ml_model"
    return fallback_probability(p), "rule_based_fallback"


def score_cell(slope_deg: float, rain_24h_mm: float, soil_moisture_pct: float, historical_density: float) -> float:
    score = (
        min(soil_moisture_pct / 100, 1) * 0.35
        + min(rain_24h_mm / 250, 1) * 0.30
        + min(slope_deg / 80, 1) * 0.20
        + min(historical_density, 1) * 0.15
    )
    return round(float(np.clip(score, 0, 1)) * 100, 2)


# --- Multilingual alert templates (fixed phrasing, not a translation API) ------------------

_TEMPLATES = {
    "en": "NER-SHIELD ALERT: {severity} risk near {district}. {detail}",
    "hi": "NER-SHIELD चेतावनी: {district} के पास {severity} स्तर का खतरा। {detail}",
    "as": "NER-SHIELD সতৰ্কবাণী: {district}ৰ ওচৰত {severity} মাত্ৰাৰ বিপদ। {detail}",
    "bn": "NER-SHIELD সতর্কতা: {district}-এর কাছে {severity} মাত্রার ঝুঁকি। {detail}",
}
_SEVERITY_WORDS = {
    "en": {"low": "LOW", "moderate": "MODERATE", "high": "HIGH", "critical": "CRITICAL"},
    "hi": {"low": "निम्न", "moderate": "मध्यम", "high": "उच्च", "critical": "अति गंभीर"},
    "as": {"low": "নিম্ন", "moderate": "মধ্যম", "high": "উচ্চ", "critical": "গুৰুতৰ"},
    "bn": {"low": "নিম্ন", "moderate": "মাঝারি", "high": "উচ্চ", "critical": "সংকটজনক"},
}


def localized_message(severity: str, district: str | None, detail: str) -> str:
    place = district or "an unspecified location"
    lines = []
    for lang in ALERT_LANGUAGES:
        template = _TEMPLATES.get(lang, _TEMPLATES["en"])
        word = _SEVERITY_WORDS.get(lang, _SEVERITY_WORDS["en"]).get(severity, severity.upper())
        lines.append(template.format(severity=word, district=place, detail=detail))
    return "\n".join(lines)


# --- SMS dispatch + alert pipeline ---------------------------------------------------------

logger = logging.getLogger("ner_shield")


def send_sms(to: str, body: str) -> str:
    """Sends via textbee.dev (your own Android phone's SIM, through their gateway
    app) if TEXTBEE_API_KEY is configured; otherwise returns "simulated" so the
    alert pipeline stays fully testable without a live SMS account."""
    if not TEXTBEE_API_KEY:
        return "simulated"
    payload = {"recipients": [to], "message": body}
    if TEXTBEE_DEVICE_ID:
        payload["deviceId"] = TEXTBEE_DEVICE_ID
    try:
        response = httpx.post(
            "https://api.textbee.dev/api/v1/gateway/send-sms",
            headers={"x-api-key": TEXTBEE_API_KEY},
            json=payload,
            timeout=10,
        )
        if response.status_code < 300:
            return "sent"
        logger.error("Textbee to %s failed: HTTP %s: %s", to, response.status_code, response.text)
        return "failed"
    except httpx.HTTPError as exc:
        logger.error("Textbee to %s raised an exception: %s", to, exc)
        return "failed"


def raise_alert(
    db: Session, *, source_type: str, source_id: str | None, district: str | None, severity: str, message: str
) -> Alert | None:
    if SEVERITY_ORDER.index(severity) < SEVERITY_ORDER.index(ALERT_SEVERITY_THRESHOLD):
        return None
    cooldown_cutoff = datetime.now(timezone.utc) - timedelta(minutes=ALERT_COOLDOWN_MINUTES)
    recent = (
        db.query(Alert)
        .filter(Alert.district == district, Alert.severity == severity, Alert.created_at >= cooldown_cutoff)
        .first()
    )
    if recent:
        return recent  # de-duplicated: don't re-alert the same district/severity within the cooldown window
    if not ALERT_SMS_RECIPIENTS:
        status = "no_recipients"
    else:
        results = [send_sms(to, message) for to in ALERT_SMS_RECIPIENTS]
        status = "sent" if "sent" in results else ("simulated" if "simulated" in results else "failed")
    alert = Alert(
        source_type=source_type, source_id=source_id, district=district, severity=severity,
        message=message, channel="sms", recipients=",".join(ALERT_SMS_RECIPIENTS) or None, status=status,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def upsert_risk_cell(
    db: Session, *, latitude: float, longitude: float, district: str | None,
    slope_deg: float, rain_24h_mm: float, soil_moisture_pct: float,
    historical_density: float, risk_score: float, severity: str,
) -> str:
    """Writes a live prediction onto the map as its own small grid cell, so the GIS
    heatmap actually reflects /predict calls instead of only ever showing seeded demo data."""
    cell_id = f"live-{round(latitude, 3)}-{round(longitude, 3)}"
    d = 0.0025
    wkt = (
        f"POLYGON(({longitude - d} {latitude - d},{longitude + d} {latitude - d},"
        f"{longitude + d} {latitude + d},{longitude - d} {latitude + d},"
        f"{longitude - d} {latitude - d}))"
    )
    db.execute(text("""
        INSERT INTO risk_cells (cell_id,district,geom,slope_deg,rain_24h_mm,soil_moisture_pct,historical_density,risk_score,severity)
        VALUES (:id,:district,ST_GeomFromText(:wkt,4326),:slope,:rain,:soil,:history,:score,:severity)
        ON CONFLICT (cell_id) DO UPDATE SET
            district = EXCLUDED.district, geom = EXCLUDED.geom, slope_deg = EXCLUDED.slope_deg,
            rain_24h_mm = EXCLUDED.rain_24h_mm, soil_moisture_pct = EXCLUDED.soil_moisture_pct,
            historical_density = EXCLUDED.historical_density, risk_score = EXCLUDED.risk_score,
            severity = EXCLUDED.severity, updated_at = now()
    """), {
        "id": cell_id, "district": district or "Unknown", "wkt": wkt, "slope": slope_deg,
        "rain": rain_24h_mm, "soil": soil_moisture_pct, "history": historical_density,
        "score": risk_score, "severity": severity,
    })
    db.commit()
    return cell_id


# --- Real-time monitoring loop ---------------------------------------------------------------

def fetch_live_weather(latitude: float, longitude: float) -> dict | None:
    """Real rainfall + modeled soil moisture from Open-Meteo (free, no API key required).
    Returns None on any failure so callers can fall back to the simulated random walk —
    this keeps the demo working even without internet access."""
    try:
        response = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude, "longitude": longitude,
                "hourly": "precipitation,soil_moisture_0_to_1cm",
                "past_days": 7, "forecast_days": 1, "timezone": "auto",
            },
            timeout=10,
        )
        response.raise_for_status()
        hourly = response.json()["hourly"]
        precip = [p for p in hourly.get("precipitation", []) if p is not None]
        soil = [s for s in hourly.get("soil_moisture_0_to_1cm", []) if s is not None]
        if not precip:
            return None
        return {
            "rain_24h_mm": round(sum(precip[-24:]), 2),
            "rain_3day_mm": round(sum(precip[-72:]), 2),
            "rain_7day_mm": round(sum(precip[-168:]), 2),
            "soil_moisture_pct": round(soil[-1] * 100, 2) if soil else None,
        }
    except (httpx.HTTPError, KeyError, ValueError, IndexError):
        return None


def run_monitor_tick() -> None:
    """Periodically rescoring every *monitored* risk cell (seeded stations, cell_id not
    starting with "live-") using live Open-Meteo data (by cell centroid) when
    WEATHER_PROVIDER=open_meteo, falling back to a random walk otherwise — standing in
    for a real IMD/sensor polling job until institutional IMD access exists.

    Ad-hoc "live-*" cells created by a one-off /predict call are deliberately excluded:
    they represent a specific point-in-time prediction, not a continuously-monitored
    station, so they stay at whatever value the prediction set rather than being
    silently overwritten by real (often calmer) weather a couple of minutes later."""
    with SessionLocal() as db:
        centroids = {
            row["cell_id"]: (row["lat"], row["lon"])
            for row in db.execute(text(
                "SELECT cell_id, ST_Y(ST_Centroid(geom)) AS lat, ST_X(ST_Centroid(geom)) AS lon "
                "FROM risk_cells WHERE cell_id NOT LIKE 'live-%'"
            )).mappings().all()
        }
        monitored_cells = db.query(RiskCell).filter(~RiskCell.cell_id.like("live-%")).all()
        for cell in monitored_cells:
            live = None
            if WEATHER_PROVIDER == "open_meteo":
                lat, lon = centroids.get(cell.cell_id, (None, None))
                if lat is not None:
                    live = fetch_live_weather(lat, lon)
            if live:
                cell.rain_24h_mm = live["rain_24h_mm"]
                if live["soil_moisture_pct"] is not None:
                    cell.soil_moisture_pct = live["soil_moisture_pct"]
            else:
                cell.rain_24h_mm = max(0.0, cell.rain_24h_mm + random.uniform(-15, 20))
                cell.soil_moisture_pct = float(np.clip(cell.soil_moisture_pct + random.uniform(-3, 5), 5, 100))
            cell.risk_score = score_cell(cell.slope_deg, cell.rain_24h_mm, cell.soil_moisture_pct, cell.historical_density)
            cell.severity = severity_for(cell.risk_score)
            if cell.severity in ("high", "critical"):
                raise_alert(
                    db, source_type="monitor", source_id=cell.cell_id, district=cell.district, severity=cell.severity,
                    message=localized_message(
                        cell.severity, cell.district,
                        f"Live monitoring update ({'Open-Meteo' if live else 'simulated'}), current risk score {cell.risk_score}%.",
                    ),
                )
        db.commit()


async def monitor_loop() -> None:
    while True:
        try:
            await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
            run_monitor_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            continue


def seed_demo_data() -> None:
    with SessionLocal() as db:
        if db.query(RiskCell).count() > 0:
            return
        rows = [
            ("demo-001", "East Khasi Hills", "POLYGON((91.875 25.575,91.885 25.575,91.885 25.585,91.875 25.585,91.875 25.575))", 43, 168, 82, 0.72, 86, "critical"),
            ("demo-002", "East Khasi Hills", "POLYGON((91.885 25.575,91.895 25.575,91.895 25.585,91.885 25.585,91.885 25.575))", 31, 93, 64, 0.45, 58, "high"),
            ("demo-003", "East Khasi Hills", "POLYGON((91.875 25.585,91.885 25.585,91.885 25.595,91.875 25.595,91.875 25.585))", 19, 35, 42, 0.18, 24, "low"),
        ]
        statement = text("""
            INSERT INTO risk_cells
            (cell_id,district,geom,slope_deg,rain_24h_mm,soil_moisture_pct,historical_density,risk_score,severity)
            VALUES (:id,:district,ST_GeomFromText(:wkt,4326),:slope,:rain,:soil,:history,:score,:severity)
        """)
        for row in rows:
            db.execute(statement, {
                "id": row[0], "district": row[1], "wkt": row[2], "slope": row[3],
                "rain": row[4], "soil": row[5], "history": row[6], "score": row[7], "severity": row[8],
            })
        db.commit()


def seed_infrastructure() -> None:
    with SessionLocal() as db:
        if db.query(Infrastructure).count() > 0:
            return
        rows = [
            ("road", "NH-6 Shillong Bypass", "East Khasi Hills", "restricted", "LINESTRING(91.870 25.570,91.880 25.578,91.892 25.588)"),
            ("village", "Mawlai", "East Khasi Hills", None, "POINT(91.878 25.582)"),
            ("village", "Nongthymmai", "East Khasi Hills", None, "POINT(91.887 25.579)"),
            ("hospital", "Civil Hospital Shillong", "East Khasi Hills", None, "POINT(91.883 25.577)"),
        ]
        statement = text("""
            INSERT INTO infrastructure (kind,name,district,status,geom)
            VALUES (:kind,:name,:district,:status,ST_GeomFromText(:wkt,4326))
        """)
        for kind, name, district, status, wkt in rows:
            db.execute(statement, {"kind": kind, "name": name, "district": district, "status": status, "wkt": wkt})
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global loaded_model, monitor_task
    Base.metadata.create_all(bind=engine)  # MVP only; use Alembic migrations before production.
    if Path(MODEL_PATH).exists():
        candidate = joblib.load(MODEL_PATH)
        expected = list(getattr(candidate, "feature_names_in_", FEATURES))
        if expected != FEATURES:
            raise RuntimeError(f"Model features must exactly be {FEATURES}; got {expected}")
        loaded_model = candidate
    seed_demo_data()
    seed_infrastructure()
    if MONITOR_ENABLED:
        monitor_task = asyncio.create_task(monitor_loop())
    yield
    if monitor_task:
        monitor_task.cancel()


app = FastAPI(title="NER-SHIELD API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/health")
def health():
    return {
        "status": "ok", "model_loaded": loaded_model is not None,
        "monitor_enabled": MONITOR_ENABLED, "weather_provider": WEATHER_PROVIDER,
    }


@app.get("/api/v1/summary")
def summary(db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT severity, COUNT(*) AS count FROM risk_cells GROUP BY severity")).mappings().all()
    return {
        "risk_counts": {row["severity"]: row["count"] for row in rows},
        "demo_notice": "Replace demo cells with validated data before operations.",
    }


@app.get("/api/v1/risk-cells")
def risk_cells(min_score: float = 0, db: Session = Depends(get_db)):
    if not 0 <= min_score <= 100:
        raise HTTPException(422, "min_score must be 0 to 100")
    rows = db.execute(text("""
        SELECT cell_id,district,slope_deg,rain_24h_mm,soil_moisture_pct,historical_density,
               risk_score,severity,updated_at,ST_AsGeoJSON(geom) AS geometry
        FROM risk_cells WHERE risk_score >= :score ORDER BY risk_score DESC
    """), {"score": min_score}).mappings().all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(row["geometry"]),
                "properties": {
                    key: (value.isoformat() if hasattr(value, "isoformat") else value)
                    for key, value in row.items() if key != "geometry"
                },
            }
            for row in rows
        ],
    }


@app.get("/api/v1/infrastructure")
def infrastructure(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, kind, name, district, status, ST_AsGeoJSON(geom) AS geometry FROM infrastructure
    """)).mappings().all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(row["geometry"]),
                "properties": {key: value for key, value in row.items() if key != "geometry"},
            }
            for row in rows
        ],
    }


@app.get("/api/v1/road-status")
def road_status(db: Session = Depends(get_db)):
    rows = db.execute(text(
        "SELECT status, COUNT(*) AS count FROM infrastructure WHERE kind = 'road' GROUP BY status"
    )).mappings().all()
    return {(row["status"] or "unknown"): row["count"] for row in rows}


@app.get("/api/v1/forecast")
def forecast(district: str, db: Session = Depends(get_db)):
    rows = (
        db.query(PredictionLog)
        .filter(PredictionLog.district == district)
        .order_by(PredictionLog.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "district": district,
        "note": "Recent modeled risk readings for this district, not a live meteorological forecast (no IMD feed connected).",
        "points": [
            {
                "risk_score": r.risk_score, "severity": r.severity, "rain_24h_mm": r.rain_24h_mm,
                "source": r.source, "created_at": r.created_at.isoformat(),
            }
            for r in reversed(rows)
        ],
    }


@app.get("/api/v1/priorities")
def priorities(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT rc.cell_id, rc.district, rc.severity, rc.risk_score,
               COUNT(i.id) AS nearby_infrastructure,
               ROUND((rc.risk_score * (1 + 0.2 * COUNT(i.id)))::numeric, 2) AS priority_score
        FROM risk_cells rc
        LEFT JOIN infrastructure i ON ST_DWithin(rc.geom, i.geom, 0.02)
        GROUP BY rc.cell_id, rc.district, rc.severity, rc.risk_score
        ORDER BY priority_score DESC
        LIMIT 50
    """)).mappings().all()
    return [dict(row) for row in rows]


@app.get("/api/v1/reports", response_model=list[ReportOut])
def reports(db: Session = Depends(get_db)):
    return db.query(FieldReport).order_by(FieldReport.created_at.desc()).limit(200).all()


@app.get("/api/v1/alerts", response_model=list[AlertOut])
def alerts(db: Session = Depends(get_db)):
    return db.query(Alert).order_by(Alert.created_at.desc()).limit(100).all()


@app.post("/api/v1/uploads", dependencies=[Depends(require_api_key)])
async def upload_image(file: UploadFile = File(...)):
    allowed_types = {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/webm"}
    if file.content_type not in allowed_types:
        raise HTTPException(415, "JPEG, PNG, WebP, MP4, or WebM only")
    suffix = Path(file.filename or "photo.jpg").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".webm"}:
        raise HTTPException(415, "Unsupported filename extension")
    target = Path(UPLOAD_DIR) / f"{uuid.uuid4().hex}{suffix}"
    limit = MAX_UPLOAD_MB * 1024 * 1024
    total = 0
    with target.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, f"Maximum upload is {MAX_UPLOAD_MB} MB")
            handle.write(chunk)
    return {"url": f"/uploads/{target.name}"}


@app.post("/api/v1/reports", response_model=ReportOut, status_code=201, dependencies=[Depends(require_api_key)])
def create_report(payload: ReportCreate, db: Session = Depends(get_db)):
    report = FieldReport(**payload.model_dump())
    db.add(report)
    db.commit()
    db.refresh(report)
    if report.severity in ("high", "critical"):
        # A verified-severity citizen/field report is real ground truth — reflect it on the
        # map and severity counts too, not just the alerts panel. Uses a representative score
        # for the severity band since a report doesn't carry raw sensor readings; this cell's
        # cell_id starts with "live-" so the monitor loop leaves it alone (see run_monitor_tick).
        upsert_risk_cell(
            db, latitude=report.latitude, longitude=report.longitude, district=report.district,
            slope_deg=0.0, rain_24h_mm=0.0, soil_moisture_pct=0.0, historical_density=0.3,
            risk_score=SEVERITY_MIDPOINT_SCORE[report.severity], severity=report.severity,
        )
        raise_alert(
            db, source_type="field_report", source_id=str(report.id), district=report.district,
            severity=report.severity,
            message=localized_message(
                report.severity, report.district,
                f"{report.report_type.replace('_', ' ').title()} reported: {report.description[:120]}",
            ),
        )
    return report


@app.post("/api/v1/predict", dependencies=[Depends(require_api_key)])
def prediction(payload: RiskFeatures, db: Session = Depends(get_db)):
    probability, source = predict(payload)
    score = round(probability * 100, 2)
    severity = severity_for(score)
    factors = contributing_factors(payload)

    risk_cell_id = None
    if payload.latitude is not None and payload.longitude is not None:
        risk_cell_id = upsert_risk_cell(
            db, latitude=payload.latitude, longitude=payload.longitude, district=payload.district,
            slope_deg=_or_default(payload.Slope_Angle, "Slope_Angle"),
            rain_24h_mm=_or_default(payload.Rainfall_mm, "Rainfall_mm"),
            soil_moisture_pct=_or_default(payload.Soil_Moisture_Content, "Soil_Moisture_Content") * 100,
            historical_density=min(_or_default(payload.Historical_Landslide_Count, "Historical_Landslide_Count") / 6, 1),
            risk_score=score, severity=severity,
        )

    alert_triggered = False
    if payload.district:
        db.add(PredictionLog(
            district=payload.district, risk_score=score, severity=severity,
            rain_24h_mm=_or_default(payload.Rainfall_mm, "Rainfall_mm"), source=source,
        ))
        db.commit()
        alert = raise_alert(
            db, source_type="prediction", source_id=None, district=payload.district, severity=severity,
            message=localized_message(severity, payload.district, f"Predicted risk {score}%. Factors: {', '.join(factors)}."),
        )
        alert_triggered = alert is not None

    return {
        "probability": round(probability, 4),
        "risk_score": score,
        "severity": severity,
        "source": source,
        "contributing_factors": factors,
        "alert_triggered": alert_triggered,
        "risk_cell_id": risk_cell_id,
        "imputed_fields": missing_features(payload),
    }


_last_forecast_error: list[str | None] = [None]  # temporary diagnostic, see fetch_rainfall_forecast


def fetch_rainfall_forecast(latitude: float, longitude: float) -> list[float] | None:
    """Forecasted daily rainfall totals (today + next 7 days) from Open-Meteo (free,
    keyless). Used to project risk forward by feeding real forecast rainfall into the
    trained model, rather than a naive trend extrapolation."""
    try:
        response = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude, "longitude": longitude,
                "hourly": "precipitation", "past_days": 0, "forecast_days": 8, "timezone": "auto",
            },
            timeout=10,
        )
        response.raise_for_status()
        precip = response.json()["hourly"].get("precipitation", [])
        if len(precip) < 192:  # need 8 full days of hourly data
            _last_forecast_error[0] = f"only got {len(precip)} hourly entries (need 192)"
            return None
        return [sum(precip[d * 24:(d + 1) * 24]) for d in range(8)]
    except Exception as exc:  # noqa: BLE001 - temporary broad catch to diagnose a live failure
        _last_forecast_error[0] = repr(exc)
        return None


def project_future_risk(cell: RiskCell, daily_rain: list[float], horizon_days: int) -> tuple[float, str] | None:
    """Re-scores the trained model as if `horizon_days` from now, substituting Open-Meteo's
    forecasted rainfall for that day while holding this cell's known slope/soil/history at
    their current values (everything else is imputed, same as any partial /predict call)."""
    if horizon_days >= len(daily_rain):
        return None
    features = RiskFeatures(
        Rainfall_mm=daily_rain[horizon_days],
        Rainfall_3Day=sum(daily_rain[max(0, horizon_days - 2):horizon_days + 1]),
        Rainfall_7Day=sum(daily_rain[max(0, horizon_days - 6):horizon_days + 1]),
        Slope_Angle=cell.slope_deg,
        Soil_Saturation=min(cell.soil_moisture_pct / 100, 1),
        Soil_Moisture_Content=min(cell.soil_moisture_pct / 100, 1),
        Historical_Landslide_Count=min(cell.historical_density * 6, 20),
    )
    probability, source = predict(features)
    return round(probability * 100, 2), source


@app.get("/api/v1/outlook")
def outlook(district: str, db: Session = Depends(get_db)):
    """Probability + a heuristic 'days to critical' estimate for a district, extrapolated
    from a simple linear trend across its recent /predict readings. This is explicitly a
    trend estimate from a handful of point-in-time readings, not a validated time-series
    forecast — landslide timing prediction is a genuinely hard problem no part of this
    system claims to solve rigorously. Separately, probability_3day/7day are a genuine
    weather-linked projection using Open-Meteo's actual forecast rainfall (see
    project_future_risk) — still bounded by that forecast's own accuracy at this range."""
    projection = {
        "probability_3day": None, "severity_3day": None,
        "probability_7day": None, "severity_7day": None,
        "weather_note": "No risk cell found for this district yet — submit a /predict with latitude/longitude first.",
    }
    representative_cell = (
        db.query(RiskCell).filter(RiskCell.district == district).order_by(RiskCell.risk_score.desc()).first()
    )
    if representative_cell is not None:
        centroid = db.execute(text(
            "SELECT ST_Y(ST_Centroid(geom)) AS lat, ST_X(ST_Centroid(geom)) AS lon FROM risk_cells WHERE cell_id = :id"
        ), {"id": representative_cell.cell_id}).mappings().first()
        daily_rain = fetch_rainfall_forecast(centroid["lat"], centroid["lon"]) if centroid else None
        if daily_rain:
            r3 = project_future_risk(representative_cell, daily_rain, 3)
            r7 = project_future_risk(representative_cell, daily_rain, 7)
            if r3:
                projection["probability_3day"] = round(r3[0] / 100, 4)
                projection["severity_3day"] = severity_for(r3[0])
            if r7:
                projection["probability_7day"] = round(r7[0] / 100, 4)
                projection["severity_7day"] = severity_for(r7[0])
            projection["weather_note"] = (
                "3/7-day figures use Open-Meteo's forecasted rainfall applied to this district's current "
                "terrain/soil profile through the trained model — a genuine weather-linked projection, "
                "bounded by that forecast's own accuracy at this range, not a guarantee."
            )
        else:
            projection["weather_note"] = (
                f"Could not fetch forecast weather for this district's location right now. "
                f"[debug: {_last_forecast_error[0]}]"
            )

    rows = list(reversed(
        db.query(PredictionLog)
        .filter(PredictionLog.district == district)
        .order_by(PredictionLog.created_at.desc())
        .limit(10)
        .all()
    ))
    if not rows:
        return {
            "district": district, "probability": None, "severity": None, "days_to_critical": None,
            "note": "No readings logged yet for this district — submit a /predict call with this district set first.",
            **projection,
        }
    latest = rows[-1]
    if len(rows) < 2:
        return {
            "district": district, "probability": round(latest.risk_score / 100, 4), "severity": latest.severity,
            "trend_per_day": None, "days_to_critical": None,
            "note": "Only one reading so far — need at least two over time to estimate a trend.",
            **projection,
        }

    t0 = rows[0].created_at
    xs = [(r.created_at - t0).total_seconds() / 86400 for r in rows]  # days since first reading
    ys = [r.risk_score for r in rows]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom if denom else 0.0

    days_to_critical = None
    if latest.risk_score >= 75:
        days_to_critical = 0.0
    elif slope > 0.01:
        estimate = (75 - latest.risk_score) / slope
        days_to_critical = round(estimate, 1) if estimate <= 365 else None

    return {
        "district": district,
        "probability": round(latest.risk_score / 100, 4),
        "severity": latest.severity,
        "trend_per_day": round(slope, 2),
        "days_to_critical": days_to_critical,
        "readings_used": n,
        "note": "Heuristic linear-trend estimate from recent modeled readings for this district — not a validated forecast.",
        **projection,
    }
