from app.main import RiskFeatures, fallback_probability, severity_for

def test_risk_increases_when_triggers_increase():
    low = RiskFeatures(rain_1h_mm=0, rain_24h_mm=0, rain_72h_mm=0, soil_moisture_pct=10, slope_deg=5, historical_density=0, distance_to_road_m=1000, vegetation_index=.8)
    high = RiskFeatures(rain_1h_mm=80, rain_24h_mm=220, rain_72h_mm=500, soil_moisture_pct=90, slope_deg=55, historical_density=.9, distance_to_road_m=5, vegetation_index=.1)
    assert fallback_probability(high) > fallback_probability(low)
    assert severity_for(86) == "critical"