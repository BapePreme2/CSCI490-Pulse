# The Pulse agent as a container image, used for the local test fleet
# (scripts/test-fleet.sh, OPS-03). Build from the repo root:
#   docker build -t pulse-agent:dev agent
# and mount a config file at /etc/pulse-agent/config.yaml.
FROM python:3.12-slim

WORKDIR /opt/pulse-agent
COPY pyproject.toml ./
COPY pulse_agent ./pulse_agent
RUN pip install --no-cache-dir . && rm -rf build *.egg-info && useradd --system --no-create-home pulse

USER pulse
ENTRYPOINT ["pulse-agent", "--config", "/etc/pulse-agent/config.yaml"]
