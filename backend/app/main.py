
import json
import os
import shutil
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
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]
MODEL_PATH = os.getenv("MODEL_PATH", "/app/models/risk_model.joblib")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "8"))
CORS_ORIGINS = [value.strip() for value in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")]
FEATURES = ["rain_1h_mm", "rain_24h_mm", "rain_72h_mm", "soil_moisture_pct", "slope_deg", "historical_density", "distance_to_road_m", "vegetation_index"]
Severity = Literal["low", "moderate", "high", "critical"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

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
    rain_1h_mm: float = Field(ge=0, le=1000)
    rain_24h_mm: float = Field(ge=0, le=2000)
    rain_72h_mm: float = Field(ge=0, le=4000)
    soil_moisture_pct: float = Field(ge=0, le=100)
    slope_deg: float = Field(ge=0, le=90)
    historical_density: float = Field(ge=0, le=1)
    distance_to_road_m: float = Field(ge=0, le=100000)
    vegetation_index: float = Field(ge=-1, le=1)

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
    if score >= 75: return "critical"
    if score >= 55: return "high"
    if score >= 30: return "moderate"
    return "low"

def fallback_probability(p: RiskFeatures) -> float:
    # Transparent fallback only; never label this as a trained prediction.
    score = (min(p.rain_1h_mm / 60, 1) * .10 + min(p.rain_24h_mm / 180, 1) * .25 + min(p.rain_72h_mm / 450, 1) * .20 + min(p.soil_moisture_pct / 100, 1) * .15 + min(p.slope_deg / 60, 1) * .15 + min(p.historical_density, 1) * .10 + (1 - min(p.distance_to_road_m / 500, 1)) * .03 + (1 - max(min(p.vegetation_index, 1), -1)) / 2 * .02)
    return float(np.clip(score, 0, 1))

def contributing_factors(p: RiskFeatures) -> list[str]:
    out = []
    if p.rain_24h_mm >= 100: out.append("heavy 24-hour rainfall")
    if p.rain_72h_mm >= 200: out.append("prolonged 72-hour rainfall")
    if p.soil_moisture_pct >= 70: out.append("high soil moisture")
    if p.slope_deg >= 30: out.append("steep slope")
    if p.historical_density >= .5: out.append("high historical landslide density")
    if p.distance_to_road_m <= 50: out.append("close to road/infrastructure")
    return out or ["no dominant trigger detected"]

def predict(p: RiskFeatures) -> tuple[float, str]:
    if Path(MODEL_PATH).exists():
        model = joblib.load(MODEL_PATH)
        row = np.array([[getattr(p, feature) for feature in FEATURES]])
        return float(model.predict_proba(row)[0][1]), "ml_model"
    return fallback_probability(p), "rule_based_fallback"

def seed_demo_data():
    with SessionLocal() as db:
        if db.query(RiskCell).count(): return
        rows = [
            ("demo-001", "East Khasi Hills", "POLYGON((91.875 25.575,91.885 25.575,91.885 25.585,91.875 25.585,91.875 25.575))", 43, 168, 82, .72, 86, "critical"),
            ("demo-002", "East Khasi Hills", "POLYGON((91.885 25.575,91.895 25.575,91.895 25.585,91.885 25.585,91.885 25.575))", 31, 93, 64, .45, 58, "high"),
            ("demo-003", "East Khasi Hills", "POLYGON((91.875 25.585,91.885 25.585,91.885 25.595,91.875 25.595,91.875 25.585))", 19, 35, 42, .18, 24, "low"),
        ]
        for row in rows:
            db.execute(text("""INSERT INTO risk_cells (cell_id,district,geom,slope_deg,rain_24h_mm,soil_moisture_pct,historical_density,risk_score,severity) VALUES (:id,:district,ST_GeomFromText(:wkt,4326),:slope,:rain,:soil,:history,:score,:severity)"""), {"id": row[0], "district": row[1], "wkt": row[2], "slope": row[3], "rain": row[4], "soil": row[5], "history": row[6], "score": row[7], "severity": row[8]})
        db.commit()

@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    seed_demo_data()
    yield

app = FastAPI(title="PaharSathi AI API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type", "Authorization"])
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/api/v1/summary")
def summary(db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT severity, COUNT(*) AS count FROM risk_cells GROUP BY severity")).mappings().all()
    return {"risk_counts": {row["severity"]: row["count"] for row in rows}, "demo_notice": "Replace demo cells with validated data before operations."}

@app.get("/api/v1/risk-cells")
def risk_cells(min_score: float = 0, db: Session = Depends(get_db)):
    if not 0 <= min_score <= 100: raise HTTPException(422, "min_score must be 0 to 100")
    rows = db.execute(text("""SELECT cell_id,district,slope_deg,rain_24h_mm,soil_moisture_pct,historical_density,risk_score,severity,updated_at,ST_AsGeoJSON(geom) AS geometry FROM risk_cells WHERE risk_score >= :score ORDER BY risk_score DESC"""), {"score": min_score}).mappings().all()
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": json.loads(row["geometry"]), "properties": {key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items() if key != "geometry"}} for row in rows]}

@app.get("/api/v1/reports", response_model=list[ReportOut])
def reports(db: Session = Depends(get_db)):
    return db.query(FieldReport).order_by(FieldReport.created_at.desc()).limit(200).all()

@app.post("/api/v1/uploads")
def upload_image(file: UploadFile = File(...)):
    if file.content_type not in {"image/jpeg", "image/png", "image/webp"}: raise HTTPException(415, "JPEG, PNG, and WebP only")
    suffix = Path(file.filename or "photo.jpg").suffix.lower() or ".jpg"
    name = f"{uuid.uuid4().hex}{suffix}"
    target = Path(UPLOAD_DIR) / name
    with target.open("wb") as handle: shutil.copyfileobj(file.file, handle)
    if target.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
        target.unlink(missing_ok=True); raise HTTPException(413, f"Maximum is {MAX_UPLOAD_MB} MB")
    return {"url": f"/uploads/{name}"}

@app.post("/api/v1/reports", response_model=ReportOut, status_code=201)
def create_report(payload: ReportCreate, db: Session = Depends(get_db)):
    report = FieldReport(**payload.model_dump())
    db.add(report); db.commit(); db.refresh(report)
    return report

@app.post("/api/v1/predict")
def prediction(payload: RiskFeatures):
    probability, source = predict(payload)
    score = round(probability * 100, 2)
    return {"probability": round(probability, 4), "risk_score": score, "severity": severity_for(score), "source": source, "contributing_factors": contributing_factors(payload)}