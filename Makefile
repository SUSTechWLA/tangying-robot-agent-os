PYTHON ?= python3.11
GO_TEST_PACKAGES := ./agent/... ./cmd/... ./console/... ./controlplane/... ./core/... ./edge/... ./fleet/... ./gen/... ./internal/... ./middleware/... ./orchestration/... ./skills/... ./tasks/... ./tests/architecture/... ./tests/contract/... ./web/...

.PHONY: setup generate generate-check build test test-go test-python test-web test-policy-sidecar lint e2e install-check demo sim-start sim-restart sim-status sim-logs sim-stop sim2real-check deploy-robot-pi production-check fleet-build fleet-cloud fleet-up fleet-sim fleet-demo fleet-handoff policy-handoff policy-faults fleet-chaos robocasa-install robocasa-install-full robocasa-smoke robocasa-web-assets robocasa-fleet robocasa-handoff test-robocasa-e2e test-robocasa-faults robocasa-acceptance robocasa-acceptance-candidate robocasa-acceptance-promote

setup:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/pip install -e '.[dev,visual]'
	go mod download

generate:
	bash scripts/generate-proto.sh

generate-check: generate
	git diff --exit-code -- gen/go python/tangying_robot_proto

build:
	mkdir -p bin
	go build -o bin/robot-agent ./cmd/robot-agent
	go build -o bin/local-agent ./cmd/local-agent

test-go:
	go test $(GO_TEST_PACKAGES)

test-python: build
	.venv/bin/pytest -q tests/contract/test_sim_real_runtime_boundary.py
	.venv/bin/pytest -q --ignore=tests/contract/test_sim_real_runtime_boundary.py

test-web:
	node --check web/app.js
	node --check web/world_view.js
	node --test web/app_test.mjs web/world_view_test.mjs

test-policy-sidecar:
	.venv/bin/pytest -q policy/sidecar/tests

test: test-go test-python test-web

lint:
	gofmt -l $$(find . -name '*.go' -not -path './gen/*' -not -path './vendor/*' -not -path './.gomodcache/*' -not -path './tangying-ai-operation-system/*') | tee /tmp/tangying-gofmt.out
	test ! -s /tmp/tangying-gofmt.out
	.venv/bin/ruff check . --extend-exclude tangying-ai-operation-system,datasets

e2e:
	.venv/bin/pytest tests/e2e -q

install-check:
	bash -n install.sh scripts/install/*.sh scripts/demo.sh scripts/sim-stack.sh scripts/robot-pi-quick-deploy.sh scripts/robot-pi-preflight.sh scripts/deploy-alicloud.sh scripts/fleet-up.sh scripts/fleet-sim.sh scripts/fleet-certs.sh
	.venv/bin/pytest tests/install -q

demo:
	bash scripts/demo.sh

sim-start: build
	bash scripts/sim-stack.sh start

sim-restart: build
	bash scripts/sim-stack.sh restart

sim-status:
	bash scripts/sim-stack.sh status

sim-logs:
	bash scripts/sim-stack.sh logs

sim-stop:
	bash scripts/sim-stack.sh stop

sim2real-check:
	.venv/bin/pytest tests/e2e -q
	.venv/bin/python scripts/run_simulation_acceptance.py --episodes 30 --seed 20260817

deploy-robot-pi:
	bash scripts/robot-pi-quick-deploy.sh

production-check:
	sudo robot-agent production-check robot-pi

fleet-build:
	mkdir -p bin
	go build -o bin/fleet-control-plane ./cmd/fleet-control-plane
	go build -o bin/edge-worker ./cmd/edge-worker

fleet-up:
	bash scripts/fleet-up.sh up

fleet-sim:
	bash scripts/fleet-sim.sh start

fleet-demo:
	bash scripts/fleet-sim.sh demo

fleet-handoff:
	bash scripts/fleet-sim.sh handoff

policy-handoff:
	.venv/bin/pytest -q tests/e2e/test_policy_handoff.py

policy-faults:
	.venv/bin/pytest -q tests/e2e/test_policy_faults.py

fleet-chaos:
	.venv/bin/python scripts/run_fleet_harness.py --scenario all --output artifacts/fleet-harness/manual

fleet-cloud:
	cd deploy/cloud && docker compose up -d --build

robocasa-install:
	bash scripts/setup-robocasa.sh

robocasa-install-full:
	ROBOCASA_ASSET_PROFILE=full bash scripts/setup-robocasa.sh

robocasa-smoke:
	PYTHONNOUSERSITE=1 conda run -n "$${ROBOCASA_ENV_NAME:-tangying-robocasa}" python scripts/robocasa-smoke.py

robocasa-web-assets:
	PYTHONNOUSERSITE=1 conda run --no-capture-output -n "$${ROBOCASA_ENV_NAME:-tangying-robocasa}" python scripts/export_robocasa_web_assets.py

robocasa-fleet:
	bash scripts/robocasa-fleet.sh start

robocasa-handoff:
	bash scripts/robocasa-fleet.sh handoff

test-robocasa-e2e:
	PYTHONNOUSERSITE=1 conda run --no-capture-output -n "$${ROBOCASA_ENV_NAME:-tangying-robocasa}" pytest -q tests/e2e/test_robocasa_handoff.py

test-robocasa-faults:
	PYTHONNOUSERSITE=1 conda run --no-capture-output -n "$${ROBOCASA_ENV_NAME:-tangying-robocasa}" pytest -q tests/e2e/test_robocasa_faults.py

robocasa-acceptance:
	PYTHONNOUSERSITE=1 $${PYTHON:-python} scripts/run_robocasa_harness.py --revalidate --output "$${ROBOCASA_ACCEPTANCE_PACK:-artifacts/robocasa-harness/round3}" --anchor tests/e2e/robocasa_golden_capture_anchor.json

robocasa-acceptance-candidate:
	PYTHONNOUSERSITE=1 conda run --no-capture-output -n "$${ROBOCASA_ENV_NAME:-tangying-robocasa}" python scripts/run_robocasa_harness.py --candidate --output "$${ROBOCASA_ACCEPTANCE_CANDIDATE:-artifacts/robocasa-harness/candidate}" --browser-evidence-timeout "$${ROBOCASA_BROWSER_CAPTURE_TIMEOUT:-300}"

robocasa-acceptance-promote:
	PYTHONNOUSERSITE=1 conda run --no-capture-output -n "$${ROBOCASA_ENV_NAME:-tangying-robocasa}" python scripts/run_robocasa_harness.py --promote-anchor --output "$${ROBOCASA_ACCEPTANCE_CANDIDATE:-artifacts/robocasa-harness/candidate}" --anchor tests/e2e/robocasa_golden_capture_anchor.json
