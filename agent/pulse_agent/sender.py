from __future__ import annotations

import logging
from urllib.parse import urlparse

import requests

from pulse_agent.metrics import Metric

logger = logging.getLogger(__name__)

MAX_METRICS_PER_REQUEST = 1000

# The server will never accept these payloads, so retrying is pointless and a
# single bad batch would otherwise block every later metric behind it.
_REJECTED_STATUSES = {400, 413, 422}
_AUTH_STATUSES = {401, 403}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class HttpSender:
    """Delivers metric batches to the ingestion API's POST /metrics.

    send() returns True when the batch is delivered (or permanently rejected
    by the server and dropped) and False when it should be retried later.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        hostname: str,
        timeout: float = 10.0,
        chunk_size: int = MAX_METRICS_PER_REQUEST,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
            logger.warning("Endpoint %s is not HTTPS; the API key is sent in plaintext", endpoint)

        self._endpoint = endpoint
        self._hostname = hostname
        self._timeout = timeout
        self._chunk_size = chunk_size
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"

    def send(self, metrics: list[Metric]) -> bool:
        for start in range(0, len(metrics), self._chunk_size):
            if not self._send_chunk(metrics[start : start + self._chunk_size]):
                return False
        return True

    def _send_chunk(self, chunk: list[Metric]) -> bool:
        payload = {"host": self._hostname, "metrics": [m.to_dict() for m in chunk]}
        try:
            # Redirects are not followed: they would turn the POST into a GET
            # and the agent would report success for data the server never saw.
            response = self._session.post(
                self._endpoint, json=payload, timeout=self._timeout, allow_redirects=False
            )
        except requests.RequestException as exc:
            logger.warning("Could not reach %s: %s", self._endpoint, exc)
            return False

        status = response.status_code
        if 200 <= status < 300:
            logger.debug("Sent %d metric(s): %s", len(chunk), response.text[:200])
            return True
        if status in _REJECTED_STATUSES:
            logger.error(
                "Server rejected %d metric(s) with HTTP %d, dropping them: %s",
                len(chunk), status, response.text[:500],
            )
            return True
        if status in _AUTH_STATUSES:
            logger.error("HTTP %d from %s: check api_key; will retry", status, self._endpoint)
        else:
            logger.warning("Send failed with HTTP %d from %s; will retry", status, self._endpoint)
        return False
