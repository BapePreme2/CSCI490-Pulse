import pytest
from pydantic import ValidationError

from pulse_ingest.schemas import MetricBatch, MetricIn


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
    ],
)
def test_rejects_invalid_metric(override):
    with pytest.raises(ValidationError):
        MetricIn(**valid_metric(**override))


def test_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        MetricIn(**valid_metric(extra_field="nope"))
    with pytest.raises(ValidationError):
        MetricBatch(host="web-1", metrics=[valid_metric()], surprise=True)
