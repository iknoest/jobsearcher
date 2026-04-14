import pandas as pd

from src.prerank import apply_prerank, split_by_prerank, _score_one


CONFIG = {
    "flag_weights": {
        "phygital_detected": 30,
        "pure_saas_detected": -25,
        "driver_license_flagged": -15,
    },
    "title_bonus": {
        "engineer": 10,
        "product": 10,
        "senior": 10,
    },
    "title_penalty": {
        "software": -20,
    },
    "preferred_companies": {
        "philips": 25,
    },
}


def _row(**kwargs):
    base = {
        "title": "", "company": "", "description": "",
        "phygital_detected": False, "pure_saas_detected": False,
        "driver_license_flagged": False,
    }
    base.update(kwargs)
    return base


def test_phygital_bonus():
    score, _ = _score_one(_row(phygital_detected=True), CONFIG)
    assert score == 30


def test_saas_penalty():
    score, _ = _score_one(_row(pure_saas_detected=True), CONFIG)
    assert score == -25


def test_title_bonus_stacks():
    score, _ = _score_one(_row(title="Senior Product Engineer"), CONFIG)
    assert score == 30  # senior + product + engineer


def test_software_in_title_penalty():
    score, _ = _score_one(_row(title="Software Engineer"), CONFIG)
    assert score == -10  # engineer +10, software -20


def test_preferred_company_bonus():
    score, _ = _score_one(_row(company="Philips Healthcare"), CONFIG)
    assert score == 25


def test_preferred_company_case_insensitive():
    score, _ = _score_one(_row(company="PHILIPS B.V."), CONFIG)
    assert score == 25


def test_combined_phygital_hardware_role_at_philips():
    score, _ = _score_one(
        _row(title="Senior Product Engineer", company="Philips", phygital_detected=True),
        CONFIG,
    )
    assert score == 30 + 10 + 10 + 10 + 25  # = 85


def test_pure_saas_generic_po_demoted():
    score, _ = _score_one(
        _row(title="Product Owner", pure_saas_detected=True),
        CONFIG,
    )
    assert score == 10 - 25  # product bonus - saas penalty = -15


def test_apply_prerank_sorts_desc():
    df = pd.DataFrame([
        _row(title="Software Developer", company="GenericCorp"),
        _row(title="Senior Product Engineer", company="Philips", phygital_detected=True),
        _row(title="Product Owner", pure_saas_detected=True),
    ])
    ranked = apply_prerank(df, CONFIG)
    assert ranked.iloc[0]["company"] == "Philips"
    assert list(ranked["prerank_score"]) == sorted(ranked["prerank_score"], reverse=True)


def test_split_by_prerank_top_n():
    df = pd.DataFrame([
        _row(title=f"role {i}", phygital_detected=(i < 3)) for i in range(5)
    ])
    ranked = apply_prerank(df, CONFIG)
    kept, dropped = split_by_prerank(ranked, 3)
    assert len(kept) == 3
    assert len(dropped) == 2
    assert kept["prerank_score"].min() >= dropped["prerank_score"].max()


def test_split_by_prerank_handles_small_df():
    df = pd.DataFrame([_row(title="x")])
    ranked = apply_prerank(df, CONFIG)
    kept, dropped = split_by_prerank(ranked, 10)
    assert len(kept) == 1
    assert len(dropped) == 0


def test_reasons_are_recorded():
    _, reasons = _score_one(
        _row(title="Senior Engineer", phygital_detected=True),
        CONFIG,
    )
    assert "phygital_detected+30" in reasons
    assert "title:senior+10" in reasons
    assert "title:engineer+10" in reasons


def test_no_signals_label():
    _, reasons = _score_one(_row(title="Manager", company="Unknown"), CONFIG)
    assert reasons == "no signals"
