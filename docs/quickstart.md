# Simulation quickstart

Requirements: Go 1.26, Python 3.11, Protocol Buffers compiler, and Docker for the PostgreSQL stack.

```bash
make setup
make generate

# Terminal 1: deterministic MuJoCo Robot Gateway
.venv/bin/python -m tangying_sim.server --listen 127.0.0.1:50051 --seed 7

# Terminal 2: development cloud
go run ./cmd/cloud-control-plane --dev --listen 127.0.0.1:8080

# Create and approve a task in http://127.0.0.1:8080

# Terminal 3: local agent
go run ./cmd/local-agent --dev-insecure --once \
  --cloud http://127.0.0.1:8080 \
  --robot 127.0.0.1:50051 \
  --data-dir ./artifacts/local-agent
```

For PostgreSQL-backed development:

```bash
docker compose -f deploy/docker-compose.yml up --build
```

The simulator is the default safe target. Read `docs/safety-checklist.md` and `docs/xlerobot-setup.md` before using a physical robot.
