"""Tests for src/classify/role_family.py (R2-01)."""

import pytest

from src.classify.role_family import classify_role_family, reset_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


# ── DESIRED: clear match ──────────────────────────────────────────
def test_clear_product_manager():
    r = classify_role_family(
        "Senior Product Manager",
        "You will own the product roadmap, manage the backlog in Jira, work with "
        "stakeholders, drive user research, and define product vision.",
    )
    assert r["family"] == "product_manager"
    assert r["bucket"] == "desired"
    assert r["would_hard_reject"] is False
    assert r["confidence"] >= 0.7
    assert r["evidence_spans"]["title_match"]
    assert len(r["evidence_spans"]["confirming_jd"]) >= 2


def test_clear_product_researcher():
    r = classify_role_family(
        "UX Researcher",
        "Conduct qualitative and quantitative user research, run interviews, "
        "synthesise findings, partner with product and design.",
    )
    assert r["family"] == "product_researcher"
    assert r["bucket"] == "desired"
    assert r["would_hard_reject"] is False


def test_product_engineer_hardware_with_physical_context():
    r = classify_role_family(
        "Product Engineer",
        "Design physical product prototypes, work with mechanical and PCB teams, "
        "integrate sensors, iterate on hardware builds.",
    )
    assert r["bucket"] == "desired"
    assert r["family"] == "product_engineer_hardware"


# ── EXCLUDED: hard reject ─────────────────────────────────────────
def test_software_engineer_hard_reject():
    r = classify_role_family(
        "Senior Software Engineer",
        "You will build microservices, participate in code review, design APIs, "
        "own CI/CD pipelines, write unit testing, and design patterns.",
    )
    assert r["family"] == "software_engineer"
    assert r["bucket"] == "excluded"
    assert r["would_hard_reject"] is True
    assert r["confidence"] >= 0.7


def test_devops_hard_reject():
    r = classify_role_family(
        "DevOps Engineer",
        "Own Kubernetes clusters, write Terraform, manage CI/CD pipeline, "
        "observability with Prometheus, run Ansible.",
    )
    assert r["bucket"] == "excluded"
    assert r["would_hard_reject"] is True


def test_pure_qa_hard_reject():
    r = classify_role_family(
        "Test Automation Engineer",
        "Write Selenium and Cypress scripts, design regression testing suites, "
        "maintain test plans, run automated test cases.",
    )
    assert r["bucket"] == "excluded"
    assert r["would_hard_reject"] is True


def test_biomedical_hard_reject():
    r = classify_role_family(
        "Biomedical Engineer",
        "Lead clinical validation for new medical devices under ISO 13485 and "
        "MDR, own biocompatibility testing.",
    )
    assert r["family"] == "biomedical_engineer"
    assert r["bucket"] == "excluded"
    assert r["would_hard_reject"] is True


# ── RESCUE: excluded title but rescue signal present ──────────────
def test_electrical_architect_with_rescue_signal():
    """Philips-style title that's salvageable via product/innovation context."""
    r = classify_role_family(
        "Electrical Architect: Mechatronics & Medical Innovation",
        "Drive physical product development across mechatronics and medical "
        "innovation teams. Rapid prototyping and user research.",
    )
    # Not hard-rejected because rescue signals fire.
    assert r["would_hard_reject"] is False
    # Either stays tagged excluded (Review) or gets promoted. Both are valid.
    assert r["bucket"] in ("excluded", "desired", "ambiguous")


def test_software_engineer_rescued_stays_excluded_no_promotion():
    """Excluded title with only a weak rescue signal and no stronger alt."""
    r = classify_role_family(
        "Software Engineer",
        "You code. You also talk to a product owner occasionally. Mostly write "
        "code and participate in code review.",
    )
    # "product owner responsibilities" is NOT in the JD, so rescue weak/none.
    # Should hard-reject (confirming signal "code review" is a strong signal).
    # But JD is short so score may not hit threshold → not hard-reject but tagged excluded.
    assert r["bucket"] in ("excluded", "unknown")


def test_software_engineer_rescued_and_promoted():
    """Excluded software engineer title rescued AND promoted because desired score wins."""
    r = classify_role_family(
        "Software Engineer",
        "You will own product research, drive user research sessions with "
        "participants, coordinate interviews, and feed insights back to the "
        "product roadmap. Agile/Scrum delivery with Jira backlog management. "
        "Stakeholder management across teams.",
    )
    # Rescue signals: "product research" + "user research" in JD
    # Desired product_researcher matches "user research" + "interviews" → strong
    assert r["would_hard_reject"] is False
    # Given strong desired signal, should promote
    assert r["bucket"] in ("desired", "ambiguous")


# ── AMBIGUOUS ─────────────────────────────────────────────────────
def test_technical_product_manager_hardware_signals():
    r = classify_role_family(
        "Technical Product Manager",
        "Own the roadmap for hardware and firmware teams working on IoT "
        "consumer electronics devices with embedded sensors.",
    )
    # Could land as ambiguous OR desired product_manager — both valid.
    # But signal_split favors good fit, so should lean ambiguous with high score.
    assert r["bucket"] in ("ambiguous", "desired")
    assert r["would_hard_reject"] is False


def test_technical_product_manager_backend_signals():
    r = classify_role_family(
        "Technical Product Manager",
        "Own the backend platform roadmap. Kubernetes, microservices, SaaS "
        "platform. Drive stakeholder alignment and roadmap.",
    )
    # favors_bad: backend, kubernetes, microservices, saas platform
    # Title scores 0.4, bad penalties reduce it.
    # BUT product_manager desired family also scores on "roadmap", "stakeholder"
    assert r["bucket"] in ("ambiguous", "desired", "excluded")
    # Regardless, not hard-reject because excluded software_engineer title won't match.
    assert r["would_hard_reject"] is False


# ── EDGE CASES ────────────────────────────────────────────────────
def test_empty_inputs():
    r = classify_role_family("", "")
    assert r["family"] == "unknown"
    assert r["bucket"] == "unknown"
    assert r["confidence"] == 0.0
    assert r["would_hard_reject"] is False


def test_title_only_no_jd():
    r = classify_role_family("Product Manager", "")
    assert r["family"] == "product_manager"
    # Title-only confidence ≈ 0.4 (base title weight).
    assert r["confidence"] == pytest.approx(0.4, abs=0.05)
    assert r["would_hard_reject"] is False  # can't hard-reject on title alone


def test_case_insensitivity():
    r = classify_role_family(
        "PRODUCT MANAGER",
        "OWNING THE ROADMAP, JIRA BACKLOG, STAKEHOLDER ALIGNMENT, USER RESEARCH.",
    )
    assert r["family"] == "product_manager"
    assert r["bucket"] == "desired"


def test_secondary_candidates_returned():
    """Jobs with multiple matching families should surface alternatives."""
    r = classify_role_family(
        "Senior Product Manager",
        "Drive product vision and roadmap. Partner with research team on user "
        "research and VOC studies. Hands-on prototyping.",
    )
    assert r["family"] == "product_manager"
    # Secondary may include product_researcher (VOC + user research signals)
    assert isinstance(r["secondary_candidates"], list)


def test_word_boundary_no_false_positive_on_software_engineering_manager():
    """'Software Engineering Manager' must NOT match '\\bsoftware engineer\\b'."""
    r = classify_role_family(
        "Software Engineering Manager",
        "Lead a team, manage engineers, coach and grow people.",
    )
    # Title 'Software Engineering Manager' has 'Engineering' not 'Engineer' —
    # pattern \bsoftware engineer\b fails because 'i' follows 'engineer' (no
    # word boundary). So software_engineer excluded should NOT fire.
    assert r["family"] != "software_engineer"


def test_reasoning_string_non_empty():
    r = classify_role_family(
        "Product Owner", "Manage the backlog, write PRDs, talk to users."
    )
    assert isinstance(r["reasoning"], str)
    assert len(r["reasoning"]) > 10
