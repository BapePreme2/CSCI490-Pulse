from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TAGS = 20
MAX_TAG_LEN = 200


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
