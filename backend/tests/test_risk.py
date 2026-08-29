from app.main import FEATURES, RiskFeatures, fallback_probability, missing_features, severity_for

BASE = dict(
    Rainfall_mm=20, Slope_Angle=15, Soil_Saturation=0.2, Vegetation_Cover=0.8,
    Rainfall_3Day=60, Rainfall_7Day=100, Aspect=180, Elevation_m=800, NDVI_Index=0.5,
    Land_Use_Urban=0, Land_Use_Forest=1, Land_Use_Agriculture=0,
    Earthquake_Activity=1, Proximity_to_Water=0.2, Distance_to_Road_m=500,
    Temperature_C=20, Humidity_percent=60, Soil_pH=6.5, Clay_Content=30,
    Sand_Content=40, Silt_Content=30, Soil_Erosion_Rate=10, Historical_Landslide_Count=0,
    Soil_Type_Gravel=0, Soil_Type_Sand=1, Soil_Type_Silt=0, Soil_Type_Clay=0,
    Pore_Water_Pressure_kPa=60, Soil_Moisture_Content=0.2, Microseismic_Activity=0.1,
    Acoustic_Emission_dB=40, Soil_Strain=0.001, Soil_Temperature_C=18, TDR_Reflection_Index=0.8,
)


def test_severity_boundaries():
    assert severity_for(0) == "low"
    assert severity_for(30) == "moderate"
    assert severity_for(55) == "high"
    assert severity_for(75) == "critical"


def test_wetter_steeper_and_saturated_input_is_riskier():
    low = RiskFeatures(**BASE)
    high = RiskFeatures(**{
        **BASE, "Rainfall_mm": 250, "Rainfall_3Day": 500, "Slope_Angle": 65,
        "Soil_Saturation": 0.9, "Vegetation_Cover": 0.1, "Historical_Landslide_Count": 4,
    })
    assert fallback_probability(high) > fallback_probability(low)


def test_partial_input_does_not_crash_and_is_flagged_as_imputed():
    # Only 3 of the 34 readings supplied — e.g. a field station with just a rain gauge.
    partial = RiskFeatures(Rainfall_mm=200, Rainfall_3Day=400, Rainfall_7Day=600)
    score = fallback_probability(partial)
    assert 0.0 <= score <= 1.0
    missing = missing_features(partial)
    assert "Rainfall_mm" not in missing
    assert "Slope_Angle" in missing
    assert len(missing) == len(FEATURES) - 3


def test_fully_missing_input_still_returns_a_valid_score():
    empty = RiskFeatures()
    score = fallback_probability(empty)
    assert 0.0 <= score <= 1.0
    assert len(missing_features(empty)) == len(FEATURES)