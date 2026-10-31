import pytest
from pydantic import ValidationError

from simuloom.domain.config.models import ScenarioConfig
from tests.helpers import base_config


def test_valid_config_roundtrip_and_hash():
    c = base_config()
    c2 = ScenarioConfig.model_validate_json(c.model_dump_json())
    assert c2 == c and c2.config_hash() == c.config_hash()
    assert base_config(counters=4).config_hash() != c.config_hash()
    assert c.config_hash().startswith("sha256:") and len(c.config_hash()) == 71


def test_offered_load_and_warnings():
    c = base_config()
    # lambda=0.9, E[S]=0.55*6+0.25*6+0.2*9=6.6, c=3 -> 1.98
    assert c.offered_load() == pytest.approx(0.9 * 6.6 / 3)
    assert any("unstable" in w for w in c.warnings())
    assert base_config(counters=10).warnings() == []


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("counters", 0, "greater than or equal to 1"),
        ("counters", 51, "less than or equal to 50"),
        ("horizon_minutes", 1441, "less than or equal to 1440"),
        ("warmup_minutes", 480, "warmup_minutes must be smaller"),
        ("arrival", {"process": "poisson", "rate_per_minute": 0}, "greater than 0"),
        ("arrival", {"process": "poisson", "rate_per_minute": 50}, "expected visitors"),
        ("abandonment", {"enabled": True}, "patience distribution is required"),
        ("no_show", {"enabled": True, "probability": 0}, "probability must be > 0"),
        ("queue_capacity", -1, "greater than or equal to 0"),
        ("replications", 31, "less than or equal to 30"),
        ("bogus_field", 1, "Extra inputs"),
        (
            "constraints",
            [{"id": "x", "type": "MAX_SLA_GAP", "limit": 0.1, "unit": "minute"}],
            "requires unit",
        ),
        (
            "constraints",
            [{"id": "x", "type": "MIN_SLA_ATTAINMENT", "limit": 1.5, "unit": "proportion"}],
            "within [0, 1]",
        ),
        (
            "constraints",
            [{"id": "x", "type": "MIN_SLA_ATTAINMENT", "limit": 0.5, "unit": "proportion", "group": "nope"}],
            "unknown group",
        ),
        (
            "constraints",
            [{"id": "x", "type": "MAX_SLA_GAP", "limit": 0.1, "unit": "proportion", "group": "undergrad"}],
            "does not take a group",
        ),
    ],
)
def test_invalid_fields_rejected(field, value, fragment):
    with pytest.raises(ValidationError) as ei:
        base_config(**{field: value})
    assert fragment in str(ei.value)


def test_group_rules():
    g = base_config().model_dump()["groups"]
    with pytest.raises(ValidationError, match="sum to 1"):
        base_config(groups=[{**g[0], "arrival_share": 0.5}, g[1], g[2]])
    with pytest.raises(ValidationError, match="unique"):
        base_config(groups=[g[0], g[0], g[2]])
    with pytest.raises(ValidationError, match="at least one group"):
        base_config(groups=[{**x, "enabled": False} for x in g])
    with pytest.raises(ValidationError):
        base_config(groups=[{**g[0], "key": "Bad Key"}, g[1], g[2]])
    with pytest.raises(ValidationError, match="constraint ids must be unique"):
        c = base_config().model_dump()["constraints"]
        base_config(constraints=[c[0], c[0]])
    # disabled group is excluded from share sum
    ok = base_config(groups=[{**g[0], "arrival_share": 0.75}, g[1], {**g[2], "enabled": False}])
    assert len(ok.enabled_groups()) == 2
