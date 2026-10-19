from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TAGS = 20
MAX_TAG_LEN = 200
MAX_RULE_DURATION_SECONDS = 86400

Operator = Literal[">", ">=", "<", "<="]


def _reject_nul(value: str) -> str:
    # Postgres text columns cannot store NUL characters.
    if "\x00" in value:
        raise ValueError("NUL characters are not allowed")
    return value


class MetricIn(BaseModel):
    """One sample, mirroring the agent's `Metric.to_dict()` output."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=32)
    tags: dict[str, str] = Field(default_factory=dict)
    timestamp: float = Field(gt=0, description="Unix epoch seconds")

    @field_validator("name", "unit")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return _reject_nul(value)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, tags: dict[str, str]) -> dict[str, str]:
        if len(tags) > MAX_TAGS:
            raise ValueError(f"at most {MAX_TAGS} tags per metric")
        for key, val in tags.items():
            if not key or len(key) > MAX_TAG_LEN or len(val) > MAX_TAG_LEN:
                raise ValueError(f"tag keys/values must be 1-{MAX_TAG_LEN} chars")
            _reject_nul(key)
            _reject_nul(val)
        return tags


class MetricBatch(BaseModel):
    """Body of `POST /metrics`: one agent flush."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    metrics: list[MetricIn] = Field(min_length=1, max_length=5000)

    @field_validator("host")
    @classmethod
    def _check_host(cls, value: str) -> str:
        return _reject_nul(value)


class AlertRuleIn(BaseModel):
    """An alert rule: `metric_name operator threshold` held for `duration_seconds`.

    Mirrors the `alert_rules` table, except `host` is a hostname (None means
    fleet-wide) rather than a `hosts.id`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    metric_name: str = Field(min_length=1, max_length=200)
    operator: Operator
    threshold: float = Field(allow_inf_nan=False)
    duration_seconds: int = Field(ge=0, le=MAX_RULE_DURATION_SECONDS)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    tag_filter: dict[str, str | None] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("name", "metric_name", "host")
    @classmethod
    def _check_text(cls, value: str | None) -> str | None:
        return value if value is None else _reject_nul(value)

    @field_validator("tag_filter")
    @classmethod
    def _check_tag_filter(cls, tags: dict[str, str | None]) -> dict[str, str | None]:
        if len(tags) > MAX_TAGS:
            raise ValueError(f"at most {MAX_TAGS} tags in a filter")
        if "host" in tags:
            # The write path strips the `host` tag before storing samples, so
            # a filter on it would silently never match anything.
            raise ValueError("filter on a host with the `host` field, not a tag")
        for key, val in tags.items():
            if not key or len(key) > MAX_TAG_LEN or (val is not None and len(val) > MAX_TAG_LEN):
                raise ValueError(f"tag keys/values must be 1-{MAX_TAG_LEN} chars")
            _reject_nul(key)
            if val is not None:
                _reject_nul(val)
        return tags
