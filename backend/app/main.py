import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

import joblib
import numpy as np
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
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
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reporter_role: Mapped[str] = mapped_column(String(32), default="citizen")
    verification_status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RiskFeatures(BaseModel):
    Rainfall_mm: float = Field(ge=0, le=500)
    Slope_Angle: float = Field(ge=0, le=90)
    Soil_Saturation: float = Field(ge=0, le=1)
    Vegetation_Cover: float = Field(ge=0, le=1)
    Rainfall_3Day: float = Field(ge=0, le=1000)
    Rainfall_7Day: float = Field(ge=0, le=1500)
    Aspect: float = Field(ge=0, le=360)
    Elevation_m: float = Field(ge=0, le=4000)
    NDVI_Index: float = Field(ge=-1, le=1)
    Land_Use_Urban: Literal[0, 1]
    Land_Use_Forest: Literal[0, 1]
    Land_Use_Agriculture: Literal[0, 1]
    Earthquake_Activity: float = Field(ge=0, le=10)
    Proximity_to_Water: float = Field(ge=0, le=1)
    Distance_to_Road_m: float = Field(ge=0, le=2000)
    Temperature_C: float = Field(ge=-10, le=50)
    Humidity_percent: float = Field(ge=0, le=100)
    Soil_pH: float = Field(ge=0, le=14)
    Clay_Content: float = Field(ge=0, le=100)
    Sand_Content: float = Field(ge=0, le=100)
    Silt_Content: float = Field(ge=0, le=100)
    Soil_Erosion_Rate: float = Field(ge=0, le=100)
    Historical_Landslide_Count: float = Field(ge=0, le=20)
    Soil_Type_Gravel: Literal[0, 1]
    Soil_Type_Sand: Literal[0, 1]
    Soil_Type_Silt: Literal[0, 1]
    Soil_Type_Clay: Literal[0, 1]
    Pore_Water_Pressure_kPa: float = Field(ge=0, le=300)
    Soil_Moisture_Content: float = Field(ge=0, le=1)
    Microseismic_Activity: float = Field(ge=0, le=1)
    Acoustic_Emission_dB: float = Field(ge=0, le=150)
    Soil_Strain: float = Field(ge=0, le=1)
    Soil_Temperature_C: float = Field(ge=-10, le=50)
    TDR_Reflection_Index: float = Field(ge=0, le=3)


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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def severity_for(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 30:
        return "moderate"
    return "low"


def fallback_probability(p: RiskFeatures) -> float:
    """Transparent demo score built from the 4 features that actually drive risk
    in the training data (Rainfall_mm, Slope_Angle, Soil_Saturation, Vegetation_Cover);
    it is not a trained or authoritative forecast."""
    score = (
        min(p.Soil_Saturation, 1) * 0.30
        + min(p.Rainfall_mm / 300, 1) * 0.25
        + min(p.Slope_Angle / 80, 1) * 0.20
        + (1 - min(p.Vegetation_Cover, 1)) * 0.15
        + min(p.Rainfall_3Day / 600, 1) * 0.05
        + min(p.Historical_Landslide_Count / 6, 1) * 0.05
    )
    return float(np.clip(score, 0, 1))


def contributing_factors(p: RiskFeatures) -> list[str]:
    factors: list[str] = []
    if p.Soil_Saturation >= 0.7:
        factors.append("high soil saturation")
    if p.Rainfall_mm >= 150:
        factors.append("heavy recent rainfall")
    if p.Rainfall_3Day >= 300:
        factors.append("prolonged 3-day rainfall")
    if p.Slope_Angle >= 45:
        factors.append("steep slope")
    if p.Vegetation_Cover <= 0.3:
        factors.append("sparse vegetation cover")
    if p.Historical_Landslide_Count >= 2:
        factors.append("prior landslide history in area")
    return factors or ["no dominant trigger detected"]


def predict(p: RiskFeatures) -> tuple[float, str]:
    if loaded_model is not None:
        row = np.array([[getattr(p, feature) for feature in FEATURES]])
        return float(loaded_model.predict_proba(row)[0][1]), "ml_model"
    return fallback_probability(p), "rule_based_fallback"


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    global loaded_model
    Base.metadata.create_all(bind=engine)  # MVP only; use Alembic migrations before production.
    if Path(MODEL_PATH).exists():
        candidate = joblib.load(MODEL_PATH)
        expected = list(getattr(candidate, "feature_names_in_", FEATURES))
        if expected != FEATURES:
            raise RuntimeError(f"Model features must exactly be {FEATURES}; got {expected}")
        loaded_model = candidate
    seed_demo_data()
    yield


app = FastAPI(title="PaharSathi AI API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": loaded_model is not None}


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


@app.get("/api/v1/reports", response_model=list[ReportOut])
def reports(db: Session = Depends(get_db)):
    return db.query(FieldReport).order_by(FieldReport.created_at.desc()).limit(200).all()


@app.post("/api/v1/uploads")
async def upload_image(file: UploadFile = File(...)):
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(415, "JPEG, PNG, and WebP only")
    suffix = Path(file.filename or "photo.jpg").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
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


@app.post("/api/v1/reports", response_model=ReportOut, status_code=201)
def create_report(payload: ReportCreate, db: Session = Depends(get_db)):
    report = FieldReport(**payload.model_dump())
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@app.post("/api/v1/predict")
def prediction(payload: RiskFeatures):
    probability, source = predict(payload)
    score = round(probability * 100, 2)
    return {
        "probability": round(probability, 4),
        "risk_score": score,
        "severity": severity_for(score),
        "source": source,
        "contributing_factors": contributing_factors(payload),
    }