# Deploying the Pulse Agent to a New Host

This is for putting the agent on a machine you want monitored -- a real
server, a VM, a container -- as opposed to `README.md`, which is for
developing the agent itself. Every command below was actually run while
writing this: a wheel was built, installed on a machine with no copy of
this repository, run against a real ingestion API, and supervised by a
real systemd service that was killed and confirmed to restart itself.

## Prerequisites

- Python 3.10 or newer on the target host.
- Outbound network access from the target host to the ingestion API's
  `endpoint` (HTTPS, typically port 443). No inbound ports are needed --
  the agent only ever makes outgoing requests.
- An API key from whoever runs the ingestion API (one of its
  `PULSE_API_KEYS`).

## 1. Build a wheel (on your own machine, once)

```bash
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install build
python -m build --wheel          # writes dist/pulse_agent-0.1.0-py3-none-any.whl
```

The wheel is self-contained -- it does not need this git repository on the
target host, only the one file. Copy it over:

```bash
scp dist/pulse_agent-0.1.0-py3-none-any.whl your-new-host:/tmp/
```

(If the target host already has this repo checked out, e.g. another
development machine, you can skip the wheel and `pip install -e .`
instead, same as `README.md`'s setup -- that's simpler but ties the
install to a live source checkout, which isn't appropriate for a server
that should just run the agent.)

## 2. Install it on the target host

```bash
sudo mkdir -p /opt/pulse-agent
sudo python3 -m venv /opt/pulse-agent/.venv
sudo /opt/pulse-agent/.venv/bin/pip install /tmp/pulse_agent-0.1.0-py3-none-any.whl
```

Confirm it installed without needing anything else from the repo:

```bash
/opt/pulse-agent/.venv/bin/pulse-agent --help
```

## 3. Configure it

```bash
sudo mkdir -p /etc/pulse-agent
sudo tee /etc/pulse-agent/config.yaml > /dev/null <<'EOF'
endpoint: https://your-ingest-api.example.com/metrics
api_key: PASTE-THE-REAL-KEY-HERE
hostname: null          # defaults to this machine's real hostname
environment: production
tags: {}
EOF
sudo chmod 600 /etc/pulse-agent/config.yaml
```

`chmod 600` matters: this file holds a real API key. See
`agent/README.md`'s "Labeling" section for what `hostname`/`environment`/
`tags` do and the rules on each (in particular, `tags` cannot contain
`host` or `environment` -- those have their own fields).

## 4. Test it manually before trusting it to run unattended

```bash
/opt/pulse-agent/.venv/bin/pulse-agent --config /etc/pulse-agent/config.yaml --once --log-level DEBUG
```

You should see a `"Sent N metric(s)"` line with no errors. If the API
key or endpoint is wrong, the error here is much easier to see and fix
than the generic "service failed to start" you'd get from systemd. Only
move on once this works.

## 5. Run it as a systemd service

Running `pulse-agent` directly in a terminal stops reporting the moment
you log out. For an actual deployment, supervise it with systemd so it
starts on boot and restarts itself if it ever crashes.

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin pulse-agent
sudo chown -R pulse-agent:pulse-agent /opt/pulse-agent /etc/pulse-agent
```

(A dedicated, unprivileged system user, not root -- the agent only reads
system metrics, it never needs write/admin access to anything.)

```bash
sudo tee /etc/systemd/system/pulse-agent.service > /dev/null <<'EOF'
[Unit]
Description=Pulse monitoring agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pulse-agent
Group=pulse-agent
ExecStart=/opt/pulse-agent/.venv/bin/pulse-agent --config /etc/pulse-agent/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now pulse-agent
```

Check it's actually running:

```bash
systemctl status pulse-agent          # should say "active (running)"
journalctl -u pulse-agent -f          # follow its logs live
```

This exact unit (adjusted to a throwaway path) was installed, started,
and had its process killed with `kill -9` to confirm `Restart=on-failure`
actually brings it back -- it does, with a new PID, within `RestartSec`.

## 6. Verify the host shows up

```bash
curl https://your-ingest-api.example.com/hosts/<hostname>
```

should return `"status": "online"` within a few seconds, or check the
dashboard's fleet overview page -- the new host appears there the same
way (see `dashboard/README.md`). `"status": "offline"` for longer than a
few `interval_seconds` means the agent isn't reaching the API; see
Troubleshooting below.

## Updating

```bash
python -m build --wheel      # on your build machine, after pulling the latest code
scp dist/*.whl your-host:/tmp/
# on the target host:
sudo /opt/pulse-agent/.venv/bin/pip install --force-reinstall /tmp/pulse_agent-0.1.0-py3-none-any.whl
sudo systemctl restart pulse-agent
```

## Uninstalling

```bash
sudo systemctl disable --now pulse-agent
sudo rm /etc/systemd/system/pulse-agent.service
sudo systemctl daemon-reload
sudo rm -rf /opt/pulse-agent /etc/pulse-agent
sudo userdel pulse-agent
```

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `Config error: ...` at startup | Something in `config.yaml` is wrong -- the message names the exact field. Fix it and rerun Step 4 before touching systemd. |
| Agent logs `Could not reach the ingestion API` | Check `endpoint`, check the target host can reach it at all (`curl <endpoint-without-/metrics>/health`), check outbound firewall rules. |
| Agent logs `HTTP 401 ... check api_key` | `api_key` in `config.yaml` doesn't match any key in the API's `PULSE_API_KEYS`. |
| `systemctl status` shows `failed` immediately | `journalctl -u pulse-agent -n 50` will show the actual Python error -- usually a config problem Step 4 would have caught, or wrong file paths/ownership in the unit file. |
| Host never shows `"status": "online"` | The agent process may not be running at all (check `systemctl status`), or it's running but misconfigured (check its logs) -- this endpoint reflects the database, not the agent directly. |
| `Permission denied` reading `config.yaml` | Check Step 5's `chown`/`chmod` -- the `pulse-agent` user needs read access to the config file. |
