from pulse_ingest.store import AlertRule, UnknownHostError, WriteResult


class FakeStore:
    """In-memory MetricStore/QueryStore for API tests that don't need Postgres."""

    def __init__(
        self,
        error: Exception | None = None,
        hosts: dict | None = None,
        host_summaries: list | None = None,
        known_hosts: set | None = None,
    ):
        self.batches = []
        self.error = error
        # host -> {"latest": [LatestMetric, ...], "history": {name: [HistorySeries, ...]}}
        self._hosts = hosts or {}
        self._written_hosts: set[str] = set()
        self._host_summaries = host_summaries if host_summaries is not None else []
        # Hostnames a rule is allowed to name.
        self._known_hosts = known_hosts or set()
        self._rules: dict[int, AlertRule] = {}
        self._next_rule_id = 1

    def write_batch(self, batch):
        if self.error:
            raise self.error
        self.batches.append(batch)
        new_host = batch.host not in self._written_hosts
        self._written_hosts.add(batch.host)
        return WriteResult(stored=len(batch.metrics), new_host=new_host)

    def host_exists(self, host):
        if self.error:
            raise self.error
        return host in self._hosts

    def list_hosts(self, now=None):
        if self.error:
            raise self.error
        return self._host_summaries

    def get_host_summary(self, host, now=None):
        if self.error:
            raise self.error
        return next((h for h in self._host_summaries if h.hostname == host), None)

    def get_latest(self, host):
        if self.error:
            raise self.error
        return self._hosts.get(host, {}).get("latest", [])

    def get_history(self, host, name, start, end):
        if self.error:
            raise self.error
        return self._hosts.get(host, {}).get("history", {}).get(name, [])

    def list_rules(self):
        if self.error:
            raise self.error
        return [self._rules[k] for k in sorted(self._rules)]

    def get_rule(self, rule_id):
        if self.error:
            raise self.error
        return self._rules.get(rule_id)

    def create_rule(self, rule):
        if self.error:
            raise self.error
        stored = self._to_rule(self._next_rule_id, rule, created_at=1000.0)
        self._rules[stored.id] = stored
        self._next_rule_id += 1
        return stored

    def update_rule(self, rule_id, rule):
        if self.error:
            raise self.error
        existing = self._rules.get(rule_id)
        if existing is None:
            return None
        self._rules[rule_id] = self._to_rule(rule_id, rule, created_at=existing.created_at)
        return self._rules[rule_id]

    def delete_rule(self, rule_id):
        if self.error:
            raise self.error
        return self._rules.pop(rule_id, None) is not None

    def _to_rule(self, rule_id, rule, created_at):
        if rule.host is not None and rule.host not in self._known_hosts:
            raise UnknownHostError(rule.host)
        return AlertRule(
            id=rule_id,
            name=rule.name,
            metric_name=rule.metric_name,
            operator=rule.operator,
            threshold=rule.threshold,
            duration_seconds=rule.duration_seconds,
            host=rule.host,
            tag_filter=rule.tag_filter,
            enabled=rule.enabled,
            created_at=created_at,
            updated_at=created_at + 1,
        )
