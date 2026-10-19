import pytest
from pydantic import ValidationError

from pulse_ingest.schemas import AlertRuleIn, MetricBatch, MetricIn


def valid_metric(**overrides):
    data = {
        "name": "cpu.usage",
        "value": 42.5,
        "unit": "percent",
        "tags": {"core": "0", "host": "web-1"},
        "timestamp": 1789419042.579,
    }
    data.update(overrides)
    return data


def test_accepts_agent_metric_shape():
    batch = MetricBatch(host="web-1", metrics=[valid_metric()])

    assert batch.host == "web-1"
    assert batch.metrics[0].tags["core"] == "0"


def test_tags_default_to_empty():
    data = valid_metric()
    del data["tags"]

    assert MetricIn(**data).tags == {}


def test_rejects_empty_batch():
    with pytest.raises(ValidationError):
        MetricBatch(host="web-1", metrics=[])


def test_rejects_oversized_batch():
    with pytest.raises(ValidationError):
        MetricBatch(host="web-1", metrics=[valid_metric()] * 5001)


@pytest.mark.parametrize("bad_host", ["", None])
def test_rejects_missing_host(bad_host):
    with pytest.raises(ValidationError):
        MetricBatch(host=bad_host, metrics=[valid_metric()])


@pytest.mark.parametrize(
    "override",
    [
        {"name": ""},
        {"unit": ""},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": "not-a-number"},
        {"timestamp": 0},
        {"timestamp": -5},
        {"tags": {"core": 0}},
        {"tags": {"": "x"}},
        {"tags": {f"k{i}": "v" for i in range(21)}},
        {"name": "cpu\x00usage"},
        {"unit": "per\x00cent"},
        {"tags": {"core\x00": "0"}},
        {"tags": {"core": "0\x00"}},
    ],
)
def test_rejects_invalid_metric(override):
    with pytest.raises(ValidationError):
        MetricIn(**valid_metric(**override))


def test_rejects_nul_in_host():
    with pytest.raises(ValidationError):
        MetricBatch(host="web\x00-1", metrics=[valid_metric()])


def test_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        MetricIn(**valid_metric(extra_field="nope"))
    with pytest.raises(ValidationError):
        MetricBatch(host="web-1", metrics=[valid_metric()], surprise=True)


def valid_rule(**overrides):
    data = {
        "name": "High CPU",
        "metric_name": "cpu.usage",
        "operator": ">",
        "threshold": 80,
        "duration_seconds": 300,
    }
    data.update(overrides)
    return data


def test_rule_defaults_to_fleet_wide_enabled_and_unfiltered():
    rule = AlertRuleIn(**valid_rule())

    assert rule.host is None
    assert rule.tag_filter == {}
    assert rule.enabled is True


def test_rule_accepts_host_and_tag_filter_with_absent_tag():
    rule = AlertRuleIn(**valid_rule(host="web-1", tag_filter={"core": None, "mount": "/"}))

    assert rule.host == "web-1"
    assert rule.tag_filter == {"core": None, "mount": "/"}


@pytest.mark.parametrize("operator", [">", ">=", "<", "<="])
def test_rule_accepts_each_operator(operator):
    assert AlertRuleIn(**valid_rule(operator=operator)).operator == operator


def test_rule_accepts_zero_duration_to_fire_immediately():
    assert AlertRuleIn(**valid_rule(duration_seconds=0)).duration_seconds == 0


@pytest.mark.parametrize(
    "override",
    [
        {"name": ""},
        {"metric_name": ""},
        {"operator": "=="},
        {"operator": "gt"},
        {"threshold": float("nan")},
        {"threshold": float("inf")},
        {"threshold": "high"},
        {"duration_seconds": -1},
        {"duration_seconds": 86401},
        {"duration_seconds": 1.5},
        {"host": ""},
        {"tag_filter": {"": "x"}},
        {"tag_filter": {"core": 0}},
        {"tag_filter": {f"k{i}": "v" for i in range(21)}},
        {"tag_filter": {"host": "web-1"}},
        {"name": "High\x00CPU"},
        {"metric_name": "cpu\x00usage"},
        {"host": "web\x00-1"},
        {"tag_filter": {"mount\x00": "/"}},
        {"tag_filter": {"mount": "/\x00"}},
        {"severity": "page"},
    ],
)
def test_rejects_invalid_rule(override):
    with pytest.raises(ValidationError):
        AlertRuleIn(**valid_rule(**override))


@pytest.mark.parametrize("field", ["name", "metric_name", "operator", "threshold", "duration_seconds"])
def test_rule_requires_core_fields(field):
    data = valid_rule()
    del data[field]

    with pytest.raises(ValidationError):
        AlertRuleIn(**data)
