from src.km_visa import normalize_company_name, is_km_sponsor

def test_normalize_strips_bv():
    assert normalize_company_name("ASML Netherlands B.V.") == "asml"

def test_normalize_strips_nv():
    assert normalize_company_name("Philips Electronics N.V.") == "philips electronics"

def test_normalize_strips_holding():
    assert normalize_company_name("TomTom International Holding B.V.") == "tomtom"

def test_exact_match():
    sponsors = {"asml": "12345678"}
    assert is_km_sponsor("ASML Netherlands B.V.", sponsors) == True

def test_fuzzy_match():
    sponsors = {"asml": "12345678"}
    assert is_km_sponsor("ASML", sponsors) == True

def test_no_match():
    sponsors = {"asml": "12345678"}
    assert is_km_sponsor("Random Startup XYZ", sponsors) == False

def test_fuzzy_threshold():
    sponsors = {"koninklijke philips": "87654321"}
    assert is_km_sponsor("Philip Morris", sponsors) == False
