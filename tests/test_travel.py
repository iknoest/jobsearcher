from src.travel import _estimate_by_distance, enrich_with_travel_time
import pandas as pd


def test_unknown_city_returns_empty_string():
    result = _estimate_by_distance("Hoofddorp", "SomeRandomCity")
    assert result == "", f"Expected empty string, got {result!r}"


def test_known_city_returns_int():
    result = _estimate_by_distance("Hoofddorp", "Amsterdam")
    assert result == 25


def test_enrich_no_nan():
    df = pd.DataFrame([
        {"title": "Job1", "location": "Amsterdam"},
        {"title": "Job2", "location": "SomeUnknownPlace"},
    ])
    result = enrich_with_travel_time(df)
    assert result["travel_minutes"].isna().sum() == 0
    assert result.loc[1, "travel_minutes"] == ""
