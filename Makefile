PYTHON ?= python3.11
VERSION := $(shell cat VERSION)
BUILD_VERSION ?= v$(VERSION)
GO_TEST_PACKAGES := ./agent/... ./cmd/... ./console/... ./core/... ./edge/... ./fleet/... ./gen/... ./internal/... ./middleware/... ./orchestration/... ./skills/... ./tasks/... ./tests/architecture/... ./tests/contract/... ./web/...

.PHONY: setup generate generate-check build test test-go test-python test-web test-policy-sidecar test-tool-layer tools-check lint e2e install-check demo up down stack-status stack-logs home-start home-restart home-accept home-routes sim-start sim-restart sim-status sim-logs sim-stop gazebo-house-start gazebo-house-restart gazebo-house-status gazebo-house-logs gazebo-house-stop sim2real-check deploy-robot-pi production-check fleet-build fleet-cloud fleet-up fleet-sim fleet-demo fleet-handoff policy-handoff policy-faults fleet-chaos robocasa-install robocasa-install-full robocasa-smoke robocasa-web-assets robocasa-fleet robocasa-handoff robocasa-demo test-robocasa-e2e test-robocasa-faults robocasa-acceptance robocasa-acceptance-candidate robocasa-acceptance-promote

setup:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/pip install -e '.[dev,visual,mcp]'
	go mod download
	npm ci --prefix web

generate:
	bash scripts/generate-proto.sh

generate-check: generate
	git diff --exit-code -- gen/go python/tangying_robot_proto

build:
	mkdir -p bin
	go build -ldflags "-X main.version=$(BUILD_VERSION)" -o bin/robot-agent ./cmd/robot-agent
	go build -o bin/local-agent ./cmd/local-agent

test-go:
	go test $(GO_TEST_PACKAGES)

test-python: build
	.venv/bin/pytest -q tests/contract/test_sim_real_runtime_boundary.py
	.venv/bin/pytest -q --ignore=tests/contract/test_sim_real_runtime_boundary.py

test-tool-layer:
	.venv/bin/pytest -q tests/tool_layer

tools-check:
	.venv/bin/python -m tangying_robot_gateway.llm_tools --check

test-web:
	node --check web/console_ui.js
	node --check web/app.js
	node --check web/world_view.js
	node --test web/*_test.mjs

test-policy-sidecar:
	.venv/bin/pytest -q policy/sidecar/tests

test: test-go test-python test-web

lint:
	gofmt -l $$(find agent cmd console core edge fleet internal middleware orchestration skills tasks tests web -name '*.go') | tee /tmp/tangying-gofmt.out
	test ! -s /tmp/tangying-gofmt.out
	.venv/bin/ruff check robot/gateway robot/mcp robot/ros2_ws sim policy scripts tests examples/robots
	.venv/bin/python -m tangying_robot_gateway.llm_tools --check

e2e:
	.venv/bin/pytest tests/e2e -q

install-check:
	bash -n install.sh scripts/install/*.sh scripts/demo.sh scripts/start-all.sh scripts/sim-stack.sh scripts/navigation-stack.sh scripts/robot-pi-quick-deploy.sh scripts/robot-pi-preflight.sh scripts/deploy-alicloud.sh scripts/fleet-up.sh scripts/fleet-sim.sh scripts/fleet-certs.sh
	.venv/bin/pytest tests/install -q

demo:
	bash scripts/demo.sh

up: build
	bash scripts/start-all.sh up $(START_ALL_ARGS)

down:
	bash scripts/start-all.sh down

stack-status:
	bash scripts/start-all.sh status

stack-logs:
	bash scripts/start-all.sh logs $(COMPONENT)

sim-start: build
	bash scripts/sim-stack.sh start

.PHONY: rgbd-start rgbd-restart
# State the scene explicitly. Without it the lifecycle script reuses whatever
# scene was recorded last, so a plain `make rgbd-start` could reopen the
# four-room home scene, which commissions no tabletop objects, and every
# documented tabletop task would then fail grounding.
rgbd-start: build
	bash scripts/sim-stack.sh start --perception rgbd --scene tabletop

rgbd-restart: build
	bash scripts/sim-stack.sh restart --perception rgbd --scene tabletop

.PHONY: home-start home-restart home-accept home-routes
# The delivered single-robot path: the four-room household scene with the
# kitchen fixtures the natural-language task needs. `home-accept` runs the same
# task end to end from the command line and keeps every step's observation.
home-start: build
	bash scripts/sim-stack.sh start --perception rgbd --scene home_task

home-restart: build
	bash scripts/sim-stack.sh restart --perception rgbd --scene home_task

home-accept: build
	.venv/bin/python scripts/run_home_mobile_manipulation_acceptance.py --base-url http://127.0.0.1:8787 --output artifacts/acceptance/home-mobile-run-1

# Navigation-only routes in the same house. The `home` scene commissions no
# kitchen fixtures, so these stay route verification rather than grasping.
home-routes: build
	bash scripts/sim-stack.sh restart --perception rgbd --scene home

.PHONY: navigation-start navigation-restart navigation-status navigation-logs navigation-stop
navigation-start: build
	bash scripts/navigation-stack.sh start $(NAVIGATION_ARGS)

navigation-restart: build
	bash scripts/navigation-stack.sh restart $(NAVIGATION_ARGS)

navigation-status:
	bash scripts/navigation-stack.sh status

navigation-logs:
	bash scripts/navigation-stack.sh logs $(NAVIGATION_ARGS)

navigation-stop:
	bash scripts/navigation-stack.sh stop

gazebo-house-start:
	bash scripts/gazebo-house-stack.sh start $(GAZEBO_HOUSE_ARGS)

gazebo-house-restart:
	bash scripts/gazebo-house-stack.sh restart $(GAZEBO_HOUSE_ARGS)

gazebo-house-status:
	bash scripts/gazebo-house-stack.sh status $(GAZEBO_HOUSE_ARGS)

gazebo-house-logs:
	bash scripts/gazebo-house-stack.sh logs $(GAZEBO_HOUSE_ARGS)

gazebo-house-stop:
	bash scripts/gazebo-house-stack.sh stop $(GAZEBO_HOUSE_ARGS)

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

robocasa-demo:
	bash scripts/robocasa-demo.sh

test-robocasa-e2e:
	PYTHONNOUSERSITE=1 conda run --no-capture-output -n "$${ROBOCASA_ENV_NAME:-tangying-robocasa}" pytest -q tests/e2e/test_robocasa_handoff.py

test-robocasa-faults:
	PYTHONNOUSERSITE=1 conda run --no-capture-output -n "$${ROBOCASA_ENV_NAME:-tangying-robocasa}" pytest -q tests/e2e/test_robocasa_faults.py

robocasa-acceptance:
	PYTHONNOUSERSITE=1 $${PYTHON:-python} scripts/run_robocasa_harness.py --revalidate --output "$${ROBOCASA_ACCEPTANCE_PACK:-artifacts/robocasa-harness/round4}" --anchor tests/e2e/robocasa_golden_capture_anchor.json

robocasa-acceptance-candidate:
	PYTHONNOUSERSITE=1 conda run --no-capture-output -n "$${ROBOCASA_ENV_NAME:-tangying-robocasa}" python scripts/run_robocasa_harness.py --candidate --output "$${ROBOCASA_ACCEPTANCE_CANDIDATE:-artifacts/robocasa-harness/candidate}" --browser-evidence-timeout "$${ROBOCASA_BROWSER_CAPTURE_TIMEOUT:-300}"

robocasa-acceptance-promote:
	PYTHONNOUSERSITE=1 conda run --no-capture-output -n "$${ROBOCASA_ENV_NAME:-tangying-robocasa}" python scripts/run_robocasa_harness.py --promote-anchor --output "$${ROBOCASA_ACCEPTANCE_CANDIDATE:-artifacts/robocasa-harness/candidate}" --anchor tests/e2e/robocasa_golden_capture_anchor.json
