
from app.main import RiskFeatures, fallback_probability, severity_for


def test_severity_boundaries():
    assert severity_for(0) == "low"
    assert severity_for(30) == "moderate"
    assert severity_for(55) == "high"
    assert severity_for(75) == "critical"


def test_wetter_and_steeper_input_is_riskier():
    low = RiskFeatures(
        rain_1h_mm=0, rain_24h_mm=5, rain_72h_mm=10, soil_moisture_pct=20,
        slope_deg=5, historical_density=0, distance_to_road_m=1000, vegetation_index=0.8,
    )
    high = RiskFeatures(
        rain_1h_mm=55, rain_24h_mm=170, rain_72h_mm=430, soil_moisture_pct=90,
        slope_deg=50, historical_density=0.8, distance_to_road_m=10, vegetation_index=-0.1,
    )
    assert fallback_probability(high) > fallback_probability(low)