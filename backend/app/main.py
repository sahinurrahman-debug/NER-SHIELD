import asyncio
import json
import logging
import os
import random
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from typing import Literal

import httpx
import joblib
import networkx as nx
import numpy as np
import shap
from PIL import Image, UnidentifiedImageError
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
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

# Optional: Iridium satellite fallback (see send_satellite()) for when SMS genuinely can't get
# through — e.g. a landslide has taken down both the local cell tower and the internet. Delivers
# to registered satellite modems (Rock7 RockBLOCK / Iridium SBD) at village or relay points, not
# to citizens' own phone numbers — satellite IoT hardware, not a general SMS-to-any-phone gateway.
ROCKBLOCK_IMEIS = [x.strip() for x in os.getenv("ROCKBLOCK_IMEI", "").split(",") if x.strip()]
ROCKBLOCK_USERNAME = os.getenv("ROCKBLOCK_USERNAME")
ROCKBLOCK_PASSWORD = os.getenv("ROCKBLOCK_PASSWORD")
SEVERITY_ORDER = ["low", "moderate", "high", "critical"]
# Representative risk_score for a severity band, used when a field report (which carries
# no raw sensor readings) needs to place/update a risk cell on the map.
SEVERITY_MIDPOINT_SCORE = {"low": 15.0, "moderate": 40.0, "high": 65.0, "critical": 90.0}

# Optional: real Sentinel-2 NDVI vegetation-change detection (see fetch_ndvi_change()).
# Free trial at https://www.sentinel-hub.com/ — unset means the feature returns an honest
# "not configured" response instead of faking satellite data.
SENTINELHUB_CLIENT_ID = os.getenv("SENTINELHUB_CLIENT_ID")
SENTINELHUB_CLIENT_SECRET = os.getenv("SENTINELHUB_CLIENT_SECRET")
NDVI_CACHE_DAYS = int(os.getenv("NDVI_CACHE_DAYS", "7"))

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

# The full service area: all eight North Eastern Region states, not just the seeded Meghalaya
# demo data. Served via GET /api/v1/districts so the web dashboard and mobile app both pull
# from this one list instead of maintaining their own copies (the mobile app embeds its own
# static copy instead — see mobile/lib/districts.dart — since a citizen filling out a report
# during a connectivity outage shouldn't be blocked by a failed districts-list fetch).
# District boundaries and counts change periodically as new districts are notified (especially
# in Assam and Manipur); this reflects publicly available data as of this project's development
# and should be revalidated against the latest state gazette before real deployment — the same
# honesty caveat as the seeded demo risk cells below.
NER_DISTRICTS: dict[str, list[str]] = {
    "Arunachal Pradesh": [
        "Tawang", "West Kameng", "East Kameng", "Pakke-Kessang", "Papum Pare", "Kra Daadi",
        "Kurung Kumey", "Kamle", "Lower Subansiri", "Upper Subansiri", "West Siang", "Lepa Rada",
        "East Siang", "Siang", "Upper Siang", "Lower Siang", "Lower Dibang Valley", "Dibang Valley",
        "Anjaw", "Lohit", "Namsai", "Changlang", "Tirap", "Longding", "Shi Yomi",
        "Itanagar Capital Complex",
    ],
    "Assam": [
        "Baksa", "Barpeta", "Biswanath", "Bongaigaon", "Cachar", "Charaideo", "Chirang", "Darrang",
        "Dhemaji", "Dhubri", "Dibrugarh", "Dima Hasao", "Goalpara", "Golaghat", "Hailakandi",
        "Hojai", "Jorhat", "Kamrup", "Kamrup Metropolitan", "Karbi Anglong", "Karimganj",
        "Kokrajhar", "Lakhimpur", "Majuli", "Morigaon", "Nagaon", "Nalbari", "Sivasagar",
        "South Salmara-Mankachar", "Sonitpur", "Tinsukia", "Udalguri", "West Karbi Anglong",
        "Bajali", "Tamulpur",
    ],
    "Manipur": [
        "Bishnupur", "Chandel", "Churachandpur", "Imphal East", "Imphal West", "Jiribam",
        "Kakching", "Kamjong", "Kangpokpi", "Noney", "Pherzawl", "Senapati", "Tamenglong",
        "Tengnoupal", "Thoubal", "Ukhrul",
    ],
    "Meghalaya": [
        "East Khasi Hills", "West Khasi Hills", "South West Khasi Hills", "Eastern West Khasi Hills",
        "Ri Bhoi", "East Jaintia Hills", "West Jaintia Hills", "East Garo Hills", "West Garo Hills",
        "South Garo Hills", "North Garo Hills", "South West Garo Hills",
    ],
    "Mizoram": [
        "Aizawl", "Lunglei", "Champhai", "Mamit", "Kolasib", "Serchhip", "Lawngtlai", "Saiha",
        "Khawzawl", "Hnahthial", "Saitual",
    ],
    "Nagaland": [
        "Kohima", "Dimapur", "Mokokchung", "Tuensang", "Wokha", "Zunheboto", "Phek", "Mon",
        "Longleng", "Kiphire", "Peren", "Noklak", "Chumoukedima", "Niuland", "Shamator", "Tseminyu",
    ],
    "Sikkim": [
        "East Sikkim", "West Sikkim", "North Sikkim", "South Sikkim", "Pakyong", "Soreng",
    ],
    "Tripura": [
        "West Tripura", "Sepahijala", "Gomati", "South Tripura", "Dhalai", "Khowai", "Unakoti",
        "North Tripura",
    ],
}

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
loaded_model = None
monitor_task: asyncio.Task | None = None
shap_explainer = None  # set in lifespan() if the trained model's internals can be extracted
shap_imputer = None
main_event_loop: asyncio.AbstractEventLoop | None = None  # set in lifespan(), see ConnectionManager.broadcast()


class ConnectionManager:
    """Tracks connected /ws/live dashboard clients and pushes JSON events to all of them —
    new alerts and risk-cell changes land the instant they happen, no client polling needed.
    broadcast() is deliberately a plain sync method: raise_alert(), run_monitor_tick() and
    upsert_risk_cell() are all sync code (some running in FastAPI's request threadpool, not
    the main event loop), so it hands the actual send off to the main loop via
    run_coroutine_threadsafe rather than requiring every caller to become async."""

    def __init__(self):
        self.active: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active.discard(websocket)

    async def _send_to_all(self, payload: str) -> None:
        for websocket in list(self.active):
            try:
                await websocket.send_text(payload)
            except Exception:
                self.active.discard(websocket)

    def broadcast(self, message: dict) -> None:
        if not self.active or main_event_loop is None:
            return
        payload = json.dumps(message, default=str)
        asyncio.run_coroutine_threadsafe(self._send_to_all(payload), main_event_loop)


live_manager = ConnectionManager()


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
    trust_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # see analyze_photo()
    trust_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # Satellite fallback (see send_satellite()) — only attempted when the SMS channel above
    # didn't confirm real delivery. None means it was never attempted (SMS itself succeeded).
    satellite_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    satellite_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Infrastructure(Base):
    __tablename__ = "infrastructure"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # "road" | "village" | "hospital" | "school"
    name: Mapped[str] = mapped_column(String(150))
    district: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # roads only
    population: Mapped[int | None] = mapped_column(Integer, nullable=True)  # villages only; approximate, see seed data
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


class NdviReading(Base):
    """One cached satellite vegetation-change result per risk cell — see fetch_ndvi_change()."""
    __tablename__ = "ndvi_readings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cell_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    district: Mapped[str] = mapped_column(String(100))
    before_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    before_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    after_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    after_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mean_ndvi_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_ndvi_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    ndvi_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    vegetation_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), default="unavailable")
    before_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    after_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    trust_score: int | None = None
    trust_flags: str | None = None
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
    satellite_status: str | None = None
    satellite_note: str | None = None
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


def compute_explanation(p: RiskFeatures) -> dict | None:
    """Real SHAP values from the trained model's own decision trees — exactly how much
    each reading pushed this specific prediction up or down, not a hand-written rule.
    Explains the tree model's raw score (pre-calibration); the calibrated probability
    returned alongside it is a separately-calibrated readout of the same model, so the
    two numbers won't match exactly — that's expected, not a bug."""
    if shap_explainer is None or shap_imputer is None:
        return None
    try:
        row = np.array(
            [[getattr(p, feature) if getattr(p, feature) is not None else np.nan for feature in FEATURES]],
            dtype=float,
        )
        imputed = shap_imputer.transform(row)
        raw_values = shap_explainer.shap_values(imputed)
        values = raw_values[1][0] if isinstance(raw_values, list) else raw_values[0]
        base_value = shap_explainer.expected_value
        if isinstance(base_value, (list, np.ndarray)):
            base_value = base_value[1] if len(base_value) > 1 else base_value[0]
        top_factors = sorted(
            (
                {
                    "feature": feature,
                    "value": getattr(p, feature) if getattr(p, feature) is not None else None,
                    "impact": round(float(v), 4),
                }
                for feature, v in zip(FEATURES, values)
            ),
            key=lambda item: abs(item["impact"]),
            reverse=True,
        )
        return {
            "base_value": round(float(base_value), 4),
            "top_factors": top_factors[:8],
            "note": (
                "SHAP values from the trained model's underlying decision trees: positive "
                "impact pushed this prediction toward higher risk, negative pushed it lower. "
                "Explains the model's raw score, not the calibrated probability above."
            ),
        }
    except Exception as exc:
        logger.error("SHAP explanation failed: %r", exc)
        return None


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


def send_satellite(message: str) -> tuple[str, str]:
    """Fallback delivery over Iridium satellite (Rock7's RockBLOCK Core API) when SMS didn't
    confirm real delivery — the one channel that keeps working when a landslide has taken out
    both the local cell tower and the internet. Delivers to registered satellite modems at
    village/relay points (ROCKBLOCK_IMEI), not to arbitrary citizen phone numbers — that's a
    real hardware constraint of satellite IoT messaging, not a simplification. Never raises;
    returns ("not_configured", ...) when no modem is registered, so this stays honest rather
    than pretending to deliver messages with no relay hardware in the field."""
    if not (ROCKBLOCK_IMEIS and ROCKBLOCK_USERNAME and ROCKBLOCK_PASSWORD):
        return "not_configured", (
            "No satellite relay modem registered on this deployment (ROCKBLOCK_IMEI / "
            "ROCKBLOCK_USERNAME / ROCKBLOCK_PASSWORD unset) — set these once a physical "
            "Iridium RockBLOCK modem is deployed at a village or relay point."
        )
    # Iridium SBD messages are capped at 340 bytes; truncate defensively rather than fail outright.
    payload_hex = message[:320].encode("utf-8").hex()
    results: list[str] = []
    for imei in ROCKBLOCK_IMEIS:
        try:
            response = httpx.post(
                "https://rockblock.rock7.com/rockblock/MT",
                data={"imei": imei, "username": ROCKBLOCK_USERNAME, "password": ROCKBLOCK_PASSWORD, "data": payload_hex},
                timeout=15,
            )
            body = response.text.strip()  # Rock7 replies "OK,<messageId>" or "FAILED,<code>,<reason>"
            if body.startswith("OK"):
                results.append("sent")
            else:
                results.append("failed")
                logger.error("RockBLOCK MT to %s failed: %s", imei, body)
        except httpx.HTTPError as exc:
            results.append("failed")
            logger.error("RockBLOCK MT to %s raised an exception: %s", imei, exc)
    if "sent" in results:
        return "sent", (
            f"Queued for Iridium satellite delivery to {len(ROCKBLOCK_IMEIS)} relay modem(s) — "
            "typically arrives within minutes on the modem's next satellite pass, independent of "
            "any local cellular or Wi-Fi network."
        )
    return "failed", "Satellite delivery attempt failed for all registered modems — see server logs."


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

    # Satellite is a *fallback*: only attempted once SMS has failed to confirm real delivery,
    # so a healthy SMS channel never pays the extra latency/cost of a redundant satellite send.
    satellite_status, satellite_note = (None, None)
    if status != "sent":
        satellite_status, satellite_note = send_satellite(message)

    alert = Alert(
        source_type=source_type, source_id=source_id, district=district, severity=severity,
        message=message, channel="sms", recipients=",".join(ALERT_SMS_RECIPIENTS) or None, status=status,
        satellite_status=satellite_status, satellite_note=satellite_note,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    live_manager.broadcast({
        "type": "alert",
        "alert": {
            "id": alert.id, "source_type": alert.source_type, "source_id": alert.source_id,
            "district": alert.district, "severity": alert.severity, "message": alert.message,
            "channel": alert.channel, "recipients": alert.recipients, "status": alert.status,
            "satellite_status": alert.satellite_status, "satellite_note": alert.satellite_note,
            "created_at": alert.created_at.isoformat(),
        },
    })
    return alert


def population_near(db: Session, latitude: float, longitude: float, radius_deg: float = 0.02) -> int:
    """Sums named-village population within ~2km — an honest approximation from a small
    seeded settlement list, not live census or gridded population data. Used to turn
    'this alert affects 2 nearby buildings' into 'this alert affects an estimated 45,000
    people', a substantially stronger signal for emergency-response prioritisation."""
    result = db.execute(text("""
        SELECT COALESCE(SUM(population), 0) AS total FROM infrastructure
        WHERE kind = 'village' AND population IS NOT NULL
          AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :radius)
    """), {"lat": latitude, "lon": longitude, "radius": radius_deg}).scalar()
    return int(result or 0)


# --- Photo verification (metadata + integrity, not a deep-learning content classifier) ------
# A full image classifier (recognizing "this looks like a landslide") would need PyTorch/
# TensorFlow — hundreds of MB, fragile to deploy reliably here, and not even well-trained for
# this specific domain without a custom labeled dataset. Instead this checks real, honest
# signals: does the photo's own embedded GPS match where the report claims it was taken, is
# the photo's embedded timestamp recent, and is the image actually readable/non-blank.

def _dms_to_decimal(dms: tuple, ref: str) -> float:
    d, m, s = dms
    dd = float(d) + float(m) / 60 + float(s) / 3600
    return -dd if ref in ("S", "W") else dd


def analyze_photo(path: Path, claimed_lat: float, claimed_lon: float, report_created_at: datetime) -> tuple[int | None, str | None]:
    """Returns (trust_score 0-100, human-readable flags) from EXIF GPS/timestamp
    cross-checks and a basic blank-image check — or (None, None) if the file isn't a
    readable static image (e.g. a video, which this doesn't analyze)."""
    try:
        image = Image.open(path)
        image.load()
    except (UnidentifiedImageError, OSError):
        return 0, "Could not read this as a valid image file."

    score = 60  # neutral baseline: file opened fine, no strong signal either way yet
    flags: list[str] = []

    exif = image.getexif()
    gps = exif.get_ifd(0x8825) if exif else {}
    if gps and 2 in gps and 4 in gps:
        try:
            photo_lat = _dms_to_decimal(gps[2], gps.get(1, "N"))
            photo_lon = _dms_to_decimal(gps[4], gps.get(3, "E"))
            distance_km = _haversine_km(photo_lon, photo_lat, claimed_lon, claimed_lat)
            if distance_km <= 2:
                score += 25
                flags.append(f"Photo location matches the reported location ({distance_km:.1f}km away)")
            elif distance_km <= 10:
                score += 5
                flags.append(f"Photo location is roughly near the reported location ({distance_km:.1f}km away)")
            else:
                score -= 35
                flags.append(f"Photo location is {distance_km:.0f}km from the reported location — possible mismatch")
        except (TypeError, ZeroDivisionError, KeyError):
            flags.append("Photo has GPS data in an unexpected format.")
    else:
        flags.append("Photo has no embedded GPS data (common — many phones strip this on upload).")

    exif_sub = exif.get_ifd(0x8769) if exif else {}
    taken_at = exif_sub.get(36867) or exif_sub.get(36868)  # DateTimeOriginal, else DateTimeDigitized
    if taken_at:
        try:
            photo_dt = datetime.strptime(str(taken_at), "%Y:%m:%d %H:%M:%S")
            age_days = (report_created_at.replace(tzinfo=None) - photo_dt).total_seconds() / 86400
            if -1 <= age_days <= 2:
                score += 15
                flags.append("Photo appears to have been taken recently.")
            elif age_days > 30:
                score -= 25
                flags.append(f"Photo appears to be about {int(age_days)} days old.")
        except ValueError:
            flags.append("Photo has a timestamp in an unexpected format.")
    else:
        flags.append("Photo has no embedded timestamp.")

    try:
        gray = image.convert("L")
        pixels = list(gray.getdata())
        mean = sum(pixels) / len(pixels)
        std = (sum((p - mean) ** 2 for p in pixels) / len(pixels)) ** 0.5
        if std < 8:
            score -= 40
            flags.append("Image appears blank or near-uniform — may not be a genuine photo.")
    except Exception:
        pass

    return max(0, min(100, score)), "; ".join(flags)


# --- Evacuation route planning ---------------------------------------------------------------

# Blocked roads are excluded from the graph entirely; partial-block roads are traversable
# but penalized so the algorithm strongly prefers a clear route when one exists.
ROAD_STATUS_PENALTY = {"open": 1.0, "restricted": 1.3, "partial_block": 3.0}


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def _node_key(lon: float, lat: float) -> tuple[float, float]:
    return (round(lon, 4), round(lat, 4))  # ~11m snap tolerance so shared endpoints merge


def build_road_graph(db: Session) -> nx.Graph:
    """Builds a routable graph from every non-blocked road segment in `infrastructure`.
    Deliberately excludes blocked roads rather than just penalizing them — a blocked road
    is not passable, not just slow."""
    graph = nx.Graph()
    rows = db.execute(text(
        "SELECT name, status, ST_AsGeoJSON(geom) AS geometry FROM infrastructure WHERE kind = 'road'"
    )).mappings().all()
    for row in rows:
        status = row["status"] or "open"
        if status == "blocked":
            continue
        penalty = ROAD_STATUS_PENALTY.get(status, 1.0)
        coords = json.loads(row["geometry"])["coordinates"]
        for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
            n1, n2 = _node_key(lon1, lat1), _node_key(lon2, lat2)
            dist_km = _haversine_km(lon1, lat1, lon2, lat2)
            weight = dist_km * penalty
            if graph.has_edge(n1, n2) and graph[n1][n2]["weight"] <= weight:
                continue  # keep the cheaper of two overlapping segments between the same points
            graph.add_edge(n1, n2, weight=weight, distance_km=dist_km, name=row["name"], status=status)
    return graph


def _nearest_node(graph: nx.Graph, lon: float, lat: float) -> tuple[tuple[float, float], float]:
    best, best_dist = None, float("inf")
    for node in graph.nodes:
        d = _haversine_km(lon, lat, node[0], node[1])
        if d < best_dist:
            best, best_dist = node, d
    return best, best_dist


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
    live_manager.broadcast({"type": "risk_cells", "features": _risk_cell_features_by_ids(db, [cell_id])})
    return cell_id


# --- Satellite NDVI change detection (real Sentinel-2 imagery via Sentinel Hub) ---------------
# Deforestation strips root-cohesion soil stability and is a recognised leading indicator of
# landslide risk — this pulls real Sentinel-2 L2A reflectance (not a placeholder image or a
# hand-tuned "Vegetation_Cover" input) for a risk cell's footprint, compares a recent 90-day
# window against the same season one year earlier, and reports the change in mean NDVI plus a
# colour-ramped before/after image pair. Runs on demand (not in the monitor loop) to stay
# within Sentinel Hub's free-tier processing-unit budget.
NDVI_DIR = Path(UPLOAD_DIR) / "ndvi"
NDVI_DIR.mkdir(parents=True, exist_ok=True)

_sh_token_cache: dict[str, float | str] = {}

# Statistical API: per-pixel NDVI, cloud/shadow/snow masked via the scene classification (SCL)
# band. The "dataMask" output is special-cased by Sentinel Hub — pixels where it's 0 are
# excluded from the aggregated stats, so cloudy/invalid pixels never pollute the mean.
_NDVI_EVALSCRIPT_STATS = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "SCL", "dataMask"] }],
    output: [{ id: "ndvi", bands: 1 }, { id: "dataMask", bands: 1 }],
  };
}
function evaluatePixel(s) {
  var cloudLike = s.SCL == 3 || s.SCL == 8 || s.SCL == 9 || s.SCL == 10;
  var valid = s.dataMask === 1 && !cloudLike;
  var ndvi = (s.B08 - s.B04) / (s.B08 + s.B04 + 1e-6);
  return { ndvi: [ndvi], dataMask: [valid ? 1 : 0] };
}
"""

# Process API: a single true colour-ramped NDVI PNG (brown = bare/stressed, green = dense
# vegetation) for the least-cloudy Sentinel-2 scene in the window — the visual before/after pair.
_NDVI_EVALSCRIPT_IMAGE = """
//VERSION=3
function setup() {
  return { input: [{ bands: ["B04", "B08", "dataMask"] }], output: { bands: 4 } };
}
function evaluatePixel(s) {
  var ndvi = (s.B08 - s.B04) / (s.B08 + s.B04 + 1e-6);
  var stops = [
    [-1.0, [0.53, 0.35, 0.24]], [0.0, [0.80, 0.65, 0.45]], [0.2, [0.94, 0.86, 0.44]],
    [0.4, [0.63, 0.79, 0.29]], [0.6, [0.20, 0.63, 0.17]], [1.0, [0.0, 0.30, 0.0]],
  ];
  var r = stops[0][1][0], g = stops[0][1][1], b = stops[0][1][2];
  for (var i = 0; i < stops.length - 1; i++) {
    if (ndvi >= stops[i][0] && ndvi <= stops[i + 1][0]) {
      var t = (ndvi - stops[i][0]) / (stops[i + 1][0] - stops[i][0]);
      r = stops[i][1][0] + t * (stops[i + 1][1][0] - stops[i][1][0]);
      g = stops[i][1][1] + t * (stops[i + 1][1][1] - stops[i][1][1]);
      b = stops[i][1][2] + t * (stops[i + 1][1][2] - stops[i][1][2]);
      break;
    }
  }
  return [r, g, b, s.dataMask];
}
"""


def sentinelhub_token() -> str | None:
    """OAuth2 client-credentials token, cached until ~1 minute before expiry. Never raises —
    returns None if credentials aren't configured or the auth call fails, so callers can fall
    back to an honest "not configured" response instead of a 500."""
    if not (SENTINELHUB_CLIENT_ID and SENTINELHUB_CLIENT_SECRET):
        return None
    cached = _sh_token_cache.get("token")
    if cached and float(_sh_token_cache.get("expires_at", 0)) > datetime.now(timezone.utc).timestamp():
        return str(cached)
    try:
        response = httpx.post(
            "https://services.sentinel-hub.com/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": SENTINELHUB_CLIENT_ID,
                "client_secret": SENTINELHUB_CLIENT_SECRET,
            },
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
        _sh_token_cache["token"] = body["access_token"]
        _sh_token_cache["expires_at"] = datetime.now(timezone.utc).timestamp() + body["expires_in"] - 60
        return str(body["access_token"])
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def _sh_mean_ndvi(token: str, bbox: list[float], date_from: datetime, date_to: datetime) -> float | None:
    """Mean NDVI over the AOI+window from Sentinel Hub's Statistical API, which aggregates
    every cloud-free Sentinel-2 L2A pixel captured in that window — real satellite reflectance,
    not a synthetic value. Returns None if no cloud-free coverage exists (common in NER's
    monsoon season) rather than guessing."""
    try:
        response = httpx.post(
            "https://services.sentinel-hub.com/api/v1/statistics",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "input": {
                    "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
                    "data": [{"type": "sentinel-2-l2a"}],
                },
                "aggregation": {
                    "timeRange": {
                        "from": date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to": date_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                    "aggregationInterval": {"of": f"P{max((date_to - date_from).days, 1)}D"},
                    "evalscript": _NDVI_EVALSCRIPT_STATS,
                    "resx": 10, "resy": 10,
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        total_weight, weighted_sum = 0.0, 0.0
        for interval in response.json().get("data", []):
            stats = interval.get("outputs", {}).get("ndvi", {}).get("bands", {}).get("B0", {}).get("stats")
            if not stats:
                continue
            valid = stats.get("sampleCount", 0) - stats.get("noDataCount", 0)
            if valid > 0 and stats.get("mean") is not None:
                weighted_sum += stats["mean"] * valid
                total_weight += valid
        return weighted_sum / total_weight if total_weight > 0 else None
    except (httpx.HTTPError, KeyError, ValueError, IndexError, ZeroDivisionError):
        return None


def _sh_ndvi_image(token: str, bbox: list[float], date_from: datetime, date_to: datetime, out_path: Path) -> bool:
    """Saves a colour-ramped NDVI PNG for the least-cloudy Sentinel-2 scene in the window."""
    try:
        response = httpx.post(
            "https://services.sentinel-hub.com/api/v1/process",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "input": {
                    "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
                    "data": [{
                        "type": "sentinel-2-l2a",
                        "dataFilter": {
                            "timeRange": {
                                "from": date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "to": date_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            },
                            "mosaickingOrder": "leastCC",
                        },
                    }],
                },
                "output": {"width": 256, "height": 256, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
                "evalscript": _NDVI_EVALSCRIPT_IMAGE,
            },
            timeout=30,
        )
        response.raise_for_status()
        out_path.write_bytes(response.content)
        return True
    except httpx.HTTPError:
        return False


def _ndvi_reading_out(row: NdviReading, *, configured: bool, cached: bool) -> dict:
    return {
        "cell_id": row.cell_id, "district": row.district, "configured": configured,
        "before_period": {"from": row.before_from.isoformat(), "to": row.before_to.isoformat()},
        "after_period": {"from": row.after_from.isoformat(), "to": row.after_to.isoformat()},
        "mean_ndvi_before": row.mean_ndvi_before, "mean_ndvi_after": row.mean_ndvi_after,
        "ndvi_delta": row.ndvi_delta, "vegetation_loss_pct": row.vegetation_loss_pct,
        "severity": row.severity,
        "before_image_url": row.before_image_url, "after_image_url": row.after_image_url,
        "cached": cached, "computed_at": row.created_at.isoformat(),
        "note": row.note,
    }


def fetch_ndvi_change(db: Session, cell_id: str, *, force_refresh: bool = False) -> dict:
    """Real Sentinel-2 NDVI before/after comparison for one risk cell's exact footprint.
    Caches per cell_id for NDVI_CACHE_DAYS to respect Sentinel Hub's free-tier quota; a stale
    or missing Sentinel Hub credential falls back to whatever's cached, or an honest
    "not configured" response — never a fabricated number."""
    cell = db.execute(text(
        "SELECT cell_id, district, ST_XMin(geom) AS minx, ST_YMin(geom) AS miny, "
        "ST_XMax(geom) AS maxx, ST_YMax(geom) AS maxy FROM risk_cells WHERE cell_id = :id"
    ), {"id": cell_id}).mappings().first()
    if not cell:
        raise HTTPException(404, f"No risk cell '{cell_id}'")

    existing = db.query(NdviReading).filter(NdviReading.cell_id == cell_id).first()
    if existing and not force_refresh:
        age_days = (datetime.now(timezone.utc) - existing.created_at).days
        if age_days < NDVI_CACHE_DAYS:
            return _ndvi_reading_out(existing, configured=True, cached=True)

    token = sentinelhub_token()
    if not token:
        if existing:
            return _ndvi_reading_out(existing, configured=True, cached=True)
        return {
            "cell_id": cell_id, "district": cell["district"], "configured": False,
            "before_period": None, "after_period": None,
            "mean_ndvi_before": None, "mean_ndvi_after": None,
            "ndvi_delta": None, "vegetation_loss_pct": None, "severity": "unavailable",
            "before_image_url": None, "after_image_url": None,
            "cached": False, "computed_at": None,
            "note": (
                "Sentinel Hub isn't configured on this deployment (SENTINELHUB_CLIENT_ID / "
                "SENTINELHUB_CLIENT_SECRET missing). Sign up for a free Sentinel Hub trial "
                "account to enable real Sentinel-2 NDVI vegetation-change detection."
            ),
        }

    bbox = [cell["minx"], cell["miny"], cell["maxx"], cell["maxy"]]
    now = datetime.now(timezone.utc)
    # Windows are widened to 150 days (vs. a plain 90) because parts of NER — Cherrapunji/Sohra
    # in East Khasi Hills chief among them — are among the cloudiest places on Earth; a shorter
    # window can go entirely without a cloud-free Sentinel-2 pass during monsoon months.
    after_from, after_to = now - timedelta(days=150), now
    before_from, before_to = now - timedelta(days=515), now - timedelta(days=365)

    mean_before = _sh_mean_ndvi(token, bbox, before_from, before_to)
    mean_after = _sh_mean_ndvi(token, bbox, after_from, after_to)

    # Fetch the least-cloudy scene image regardless of whether the stricter cloud-free stats
    # succeeded — a real (if imperfect) satellite photo is still worth showing even when the
    # numeric NDVI comparison can't be trusted.
    before_path = NDVI_DIR / f"{cell_id}-before.png"
    after_path = NDVI_DIR / f"{cell_id}-after.png"
    got_before_img = _sh_ndvi_image(token, bbox, before_from, before_to, before_path)
    got_after_img = _sh_ndvi_image(token, bbox, after_from, after_to, after_path)

    if mean_before is not None and mean_after is not None:
        delta = round(mean_after - mean_before, 4)
        loss_pct = round(max(0.0, -delta) / max(abs(mean_before), 0.01) * 100, 1)
        if delta <= -0.15:
            severity = "significant vegetation loss"
        elif delta <= -0.05:
            severity = "moderate vegetation loss"
        elif delta >= 0.05:
            severity = "vegetation gain"
        else:
            severity = "stable"
        note = (
            "Real Sentinel-2 L2A NDVI, cloud/shadow-masked via the scene classification band, "
            "aggregated over each date window by Sentinel Hub's Statistical API. Vegetation loss "
            "is a leading indicator, not a standalone landslide prediction — deforested slopes "
            "lose the root-cohesion that stabilises soil."
        )
    else:
        delta, loss_pct, severity = None, None, "unavailable"
        if not (got_before_img or got_after_img):
            if existing:
                return _ndvi_reading_out(existing, configured=True, cached=True)
            return {
                "cell_id": cell_id, "district": cell["district"], "configured": True,
                "before_period": {"from": before_from.isoformat(), "to": before_to.isoformat()},
                "after_period": {"from": after_from.isoformat(), "to": after_to.isoformat()},
                "mean_ndvi_before": None, "mean_ndvi_after": None,
                "ndvi_delta": None, "vegetation_loss_pct": None, "severity": "unavailable",
                "before_image_url": None, "after_image_url": None,
                "cached": False, "computed_at": None,
                "note": (
                    "No usable Sentinel-2 imagery at all for this cell in one or both 150-day "
                    "windows — common in NER's monsoon climate. Try again later."
                ),
            }
        note = (
            "Numeric NDVI comparison unavailable — no fully cloud-free Sentinel-2 pixels in one "
            "or both 150-day windows (common in NER's monsoon climate, especially Cherrapunji/"
            "Sohra). The image(s) below are the least-cloudy real Sentinel-2 scene found in each "
            "window, not a cloud-free composite."
        )

    if existing is None:
        existing = NdviReading(cell_id=cell_id)
        db.add(existing)
    existing.district = cell["district"]
    existing.before_from, existing.before_to = before_from, before_to
    existing.after_from, existing.after_to = after_from, after_to
    existing.mean_ndvi_before = round(mean_before, 4) if mean_before is not None else None
    existing.mean_ndvi_after = round(mean_after, 4) if mean_after is not None else None
    existing.ndvi_delta, existing.vegetation_loss_pct = delta, loss_pct
    existing.severity = severity
    existing.before_image_url = f"/uploads/ndvi/{before_path.name}" if got_before_img else None
    existing.after_image_url = f"/uploads/ndvi/{after_path.name}" if got_after_img else None
    existing.note = note
    db.commit()
    db.refresh(existing)
    return _ndvi_reading_out(existing, configured=True, cached=False)


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
                lat, lon = centroids.get(cell.cell_id, (None, None))
                pop = population_near(db, lat, lon) if lat is not None else 0
                pop_note = f" Estimated {pop:,} people nearby." if pop > 0 else ""
                raise_alert(
                    db, source_type="monitor", source_id=cell.cell_id, district=cell.district, severity=cell.severity,
                    message=localized_message(
                        cell.severity, cell.district,
                        f"Live monitoring update ({'Open-Meteo' if live else 'simulated'}), current risk score {cell.risk_score}%.{pop_note}",
                    ),
                )
        db.commit()
        live_manager.broadcast({
            "type": "risk_cells",
            "features": _risk_cell_features_by_ids(db, [cell.cell_id for cell in monitored_cells]),
        })


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
            # A few more seeded points elsewhere in the region — proof this isn't a Meghalaya-only
            # system, not an attempt at full NER coverage (that needs real sensor/IMD data per
            # district; see the "note" in GET /api/v1/districts).
            ("demo-004", "Papum Pare", "POLYGON((93.600 27.080,93.610 27.080,93.610 27.090,93.600 27.090,93.600 27.080))", 38, 120, 58, 0.35, 48, "moderate"),
            ("demo-005", "Aizawl", "POLYGON((92.712 23.722,92.722 23.722,92.722 23.732,92.712 23.732,92.712 23.722))", 47, 145, 71, 0.55, 68, "high"),
            ("demo-006", "Kohima", "POLYGON((94.103 25.670,94.113 25.670,94.113 25.680,94.103 25.680,94.103 25.670))", 33, 88, 48, 0.22, 32, "moderate"),
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
        # population figures are rounded approximations for these named localities, not
        # live census/gridded data — see /api/v1/priorities and alert messages for the
        # honest caveat surfaced alongside any number derived from them.
        rows = [
            ("road", "NH-6 Shillong Bypass", "East Khasi Hills", "restricted", None, "LINESTRING(91.870 25.570,91.880 25.578,91.892 25.588)"),
            ("village", "Mawlai", "East Khasi Hills", None, 45000, "POINT(91.878 25.582)"),
            ("village", "Nongthymmai", "East Khasi Hills", None, 20000, "POINT(91.887 25.579)"),
            ("hospital", "Civil Hospital Shillong", "East Khasi Hills", None, None, "POINT(91.883 25.577)"),
        ]
        statement = text("""
            INSERT INTO infrastructure (kind,name,district,status,population,geom)
            VALUES (:kind,:name,:district,:status,:population,ST_GeomFromText(:wkt,4326))
        """)
        for kind, name, district, status, population, wkt in rows:
            db.execute(statement, {
                "kind": kind, "name": name, "district": district, "status": status,
                "population": population, "wkt": wkt,
            })
        db.commit()


def seed_evacuation_roads() -> None:
    """Adds a small connected local road network on top of the single originally-seeded
    road, so /api/v1/evacuation-route has an actual graph to route through — including one
    deliberately blocked segment with a real alternate path, not a hardcoded demo route.
    Checked by name (idempotent) rather than gated on the table being empty, so it also
    backfills a database that already had infrastructure rows from before this existed."""
    with SessionLocal() as db:
        rows = [
            ("road", "Mawlai Direct Road", "East Khasi Hills", "blocked", "LINESTRING(91.878 25.582,91.883 25.577)"),
            ("road", "Mawlai-Junction Link", "East Khasi Hills", "open", "LINESTRING(91.878 25.582,91.881 25.580)"),
            ("road", "Junction-Hospital Link", "East Khasi Hills", "open", "LINESTRING(91.881 25.580,91.883 25.577)"),
            ("road", "Nongthymmai-Hospital Road", "East Khasi Hills", "open", "LINESTRING(91.887 25.579,91.883 25.577)"),
        ]
        for kind, name, district, status, wkt in rows:
            if db.execute(text("SELECT 1 FROM infrastructure WHERE name = :name"), {"name": name}).first():
                continue
            db.execute(text("""
                INSERT INTO infrastructure (kind,name,district,status,geom)
                VALUES (:kind,:name,:district,:status,ST_GeomFromText(:wkt,4326))
            """), {"kind": kind, "name": name, "district": district, "status": status, "wkt": wkt})
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global loaded_model, monitor_task, shap_explainer, shap_imputer, main_event_loop
    main_event_loop = asyncio.get_running_loop()
    Base.metadata.create_all(bind=engine)  # MVP only; use Alembic migrations before production.
    # create_all() only creates missing tables, it never alters an existing one — this
    # covers adding `population` to a database that already had `infrastructure` rows
    # from before this column existed. Safe to run every startup (idempotent).
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE infrastructure ADD COLUMN IF NOT EXISTS population INTEGER"))
        conn.execute(text("UPDATE infrastructure SET population = 45000 WHERE name = 'Mawlai' AND population IS NULL"))
        conn.execute(text("UPDATE infrastructure SET population = 20000 WHERE name = 'Nongthymmai' AND population IS NULL"))
        conn.execute(text("ALTER TABLE field_reports ADD COLUMN IF NOT EXISTS trust_score INTEGER"))
        conn.execute(text("ALTER TABLE field_reports ADD COLUMN IF NOT EXISTS trust_flags TEXT"))
        conn.execute(text("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS satellite_status VARCHAR(16)"))
        conn.execute(text("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS satellite_note TEXT"))
    if Path(MODEL_PATH).exists():
        candidate = joblib.load(MODEL_PATH)
        expected = list(getattr(candidate, "feature_names_in_", FEATURES))
        if expected != FEATURES:
            raise RuntimeError(f"Model features must exactly be {FEATURES}; got {expected}")
        loaded_model = candidate
        try:
            # CalibratedClassifierCV wraps N fitted clones of the training Pipeline (one per
            # CV fold) — pull the imputer + XGBClassifier out of the first fold so SHAP can
            # explain the actual tree model, fed through the same imputation the model itself
            # uses at inference time. Left disabled (None) rather than crashing startup if
            # sklearn's internal layout ever differs from what training produced.
            fold = candidate.calibrated_classifiers_[0]
            base_pipeline = getattr(fold, "estimator", None) or getattr(fold, "base_estimator", None)
            shap_imputer = base_pipeline.named_steps["impute"]
            shap_explainer = shap.TreeExplainer(base_pipeline.named_steps["model"])
        except Exception as exc:
            logger.error("Could not build SHAP explainer, /predict will omit explanations: %r", exc)
            shap_explainer = None
            shap_imputer = None
    seed_demo_data()
    seed_infrastructure()
    seed_evacuation_roads()
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


def _row_to_feature(row) -> dict:
    return {
        "type": "Feature",
        "geometry": json.loads(row["geometry"]),
        "properties": {
            key: (value.isoformat() if hasattr(value, "isoformat") else value)
            for key, value in row.items() if key != "geometry"
        },
    }


def _risk_cell_features_by_ids(db: Session, cell_ids: list[str]) -> list[dict]:
    """Same row shape as GET /api/v1/risk-cells, filtered to specific cells — used to push
    exactly what changed over /ws/live instead of the whole layer on every update."""
    if not cell_ids:
        return []
    rows = db.execute(text("""
        SELECT cell_id,district,slope_deg,rain_24h_mm,soil_moisture_pct,historical_density,
               risk_score,severity,updated_at,ST_AsGeoJSON(geom) AS geometry
        FROM risk_cells WHERE cell_id = ANY(:ids)
    """), {"ids": cell_ids}).mappings().all()
    return [_row_to_feature(row) for row in rows]


@app.get("/api/v1/risk-cells")
def risk_cells(min_score: float = 0, db: Session = Depends(get_db)):
    if not 0 <= min_score <= 100:
        raise HTTPException(422, "min_score must be 0 to 100")
    rows = db.execute(text("""
        SELECT cell_id,district,slope_deg,rain_24h_mm,soil_moisture_pct,historical_density,
               risk_score,severity,updated_at,ST_AsGeoJSON(geom) AS geometry
        FROM risk_cells WHERE risk_score >= :score ORDER BY risk_score DESC
    """), {"score": min_score}).mappings().all()
    return {"type": "FeatureCollection", "features": [_row_to_feature(row) for row in rows]}


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """Push channel for the dashboard: new alerts and risk-cell changes are broadcast the
    instant they happen (see ConnectionManager above), instead of the client polling on a
    timer. The client doesn't need to send anything — this just keeps the socket open."""
    await live_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        live_manager.disconnect(websocket)


@app.get("/api/v1/infrastructure")
def infrastructure(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, kind, name, district, status, population, ST_AsGeoJSON(geom) AS geometry FROM infrastructure
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


@app.get("/api/v1/districts")
def districts():
    """The full NER-SHIELD service area, grouped by state — see NER_DISTRICTS above for the
    honesty caveat on district boundaries/counts. Every district here works with /predict and
    field reports; only Meghalaya has seeded demo risk data (plus a few landslide-prone spots
    in Arunachal Pradesh, Mizoram and Nagaland — see seed_demo_data()) today, so a freshly
    selected district legitimately starts with no historical readings until real data or a
    live prediction populates one."""
    return {
        "states": [{"state": state, "districts": names} for state, names in NER_DISTRICTS.items()],
        "note": (
            "Covers all eight North Eastern Region states. Only a handful of districts have "
            "seeded demo risk data today — every other district is fully usable via /predict "
            "and field reports, it just starts with no historical readings."
        ),
    }


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
               COALESCE(SUM(i.population), 0)::int AS population_at_risk,
               ROUND((
                   rc.risk_score * (1 + 0.2 * COUNT(i.id) + 0.05 * COALESCE(SUM(i.population), 0) / 1000.0)
               )::numeric, 2) AS priority_score
        FROM risk_cells rc
        LEFT JOIN infrastructure i ON ST_DWithin(rc.geom, i.geom, 0.02)
        GROUP BY rc.cell_id, rc.district, rc.severity, rc.risk_score
        ORDER BY priority_score DESC
        LIMIT 50
    """)).mappings().all()
    return {
        "note": "population_at_risk sums approximate named-settlement figures within ~2km, not live census or gridded population data.",
        "priorities": [dict(row) for row in rows],
    }


@app.get("/api/v1/ndvi-change")
def ndvi_change(cell_id: str, force_refresh: bool = False, db: Session = Depends(get_db)):
    """Real Sentinel-2 satellite vegetation-change detection for one risk cell — see
    fetch_ndvi_change() for the Sentinel Hub integration and caching policy."""
    return fetch_ndvi_change(db, cell_id, force_refresh=force_refresh)


@app.get("/api/v1/evacuation-route")
def evacuation_route(
    from_lat: float, from_lon: float, to_lat: float | None = None, to_lon: float | None = None,
    db: Session = Depends(get_db),
):
    """Shortest safe route from a point to a destination (nearest hospital by default),
    computed with Dijkstra's algorithm over the seeded local road network — blocked roads
    are excluded entirely, partial-block roads are used only if no clear alternative exists.
    This is a real, general-purpose routing algorithm, not a hardcoded path: it recomputes
    from live road status every call, so marking a road blocked changes the answer."""
    graph = build_road_graph(db)
    if graph.number_of_nodes() == 0:
        raise HTTPException(404, "No usable road network right now — every seeded road is blocked.")

    destination_name = None
    if to_lat is None or to_lon is None:
        hospital = db.execute(text("""
            SELECT name, ST_Y(geom) AS lat, ST_X(geom) AS lon FROM infrastructure WHERE kind = 'hospital'
            ORDER BY ST_Distance(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)) LIMIT 1
        """), {"lat": from_lat, "lon": from_lon}).mappings().first()
        if not hospital:
            raise HTTPException(404, "No hospital in the infrastructure layer to route to.")
        to_lat, to_lon, destination_name = hospital["lat"], hospital["lon"], hospital["name"]

    start_node, start_snap_km = _nearest_node(graph, from_lon, from_lat)
    end_node, _ = _nearest_node(graph, to_lon, to_lat)

    try:
        path = nx.shortest_path(graph, start_node, end_node, weight="weight")
    except nx.NetworkXNoPath:
        raise HTTPException(404, "No route avoids the currently blocked roads — every path is cut off.")

    roads_used: list[str] = []
    used_partial_block: list[str] = []
    total_km = 0.0
    for a, b in zip(path, path[1:]):
        edge = graph[a][b]
        total_km += edge["distance_km"]
        if edge["name"] not in roads_used:
            roads_used.append(edge["name"])
        if edge["status"] == "partial_block" and edge["name"] not in used_partial_block:
            used_partial_block.append(edge["name"])

    return {
        "distance_km": round(total_km, 2),
        "path": [[lon, lat] for lon, lat in path],
        "roads_used": roads_used,
        "destination": destination_name,
        "used_partial_block_roads": used_partial_block,
        "start_snapped_km": round(start_snap_km, 3),
        "note": (
            "Computed from the seeded local road network only (a handful of segments near "
            "East Khasi Hills), not a full regional road graph. Blocked roads are excluded "
            "entirely; partial-block roads are used only if no clear alternative exists."
        ),
    }


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

    if report.image_url:
        local_path = Path(UPLOAD_DIR) / Path(report.image_url).name
        if local_path.suffix.lower() in {".mp4", ".webm"}:
            report.trust_score, report.trust_flags = None, "Video attachments aren't automatically analyzed yet."
        elif local_path.exists():
            report.trust_score, report.trust_flags = analyze_photo(
                local_path, report.latitude, report.longitude, report.created_at
            )
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
        pop = population_near(db, report.latitude, report.longitude)
        pop_note = f" Estimated {pop:,} people nearby." if pop > 0 else ""
        raise_alert(
            db, source_type="field_report", source_id=str(report.id), district=report.district,
            severity=report.severity,
            message=localized_message(
                report.severity, report.district,
                f"{report.report_type.replace('_', ' ').title()} reported: {report.description[:120]}{pop_note}",
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
        pop_note = ""
        if payload.latitude is not None and payload.longitude is not None:
            pop = population_near(db, payload.latitude, payload.longitude)
            pop_note = f" Estimated {pop:,} people nearby." if pop > 0 else ""
        alert = raise_alert(
            db, source_type="prediction", source_id=None, district=payload.district, severity=severity,
            message=localized_message(
                severity, payload.district, f"Predicted risk {score}%. Factors: {', '.join(factors)}.{pop_note}"
            ),
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
        "explanation": compute_explanation(payload) if source == "ml_model" else None,
    }


@app.get("/api/v1/outlook")
def outlook(district: str, db: Session = Depends(get_db)):
    """Probability + a heuristic 'days to critical' estimate for a district, extrapolated
    from a simple linear trend across its recent /predict readings. This is explicitly a
    trend estimate from a handful of point-in-time readings, not a validated time-series
    forecast — landslide timing prediction is a genuinely hard problem no part of this
    system claims to solve rigorously."""
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
        }
    latest = rows[-1]
    if len(rows) < 2:
        return {
            "district": district, "probability": round(latest.risk_score / 100, 4), "severity": latest.severity,
            "trend_per_day": None, "days_to_critical": None,
            "note": "Only one reading so far — need at least two over time to estimate a trend.",
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
    }
