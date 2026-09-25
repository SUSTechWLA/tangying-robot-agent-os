PYTHON ?= python3.11
VERSION := $(shell cat VERSION)
BUILD_VERSION ?= v$(VERSION)
GO_TEST_PACKAGES := ./agent/... ./cmd/... ./console/... ./core/... ./edge/... ./fleet/... ./gen/... ./internal/... ./middleware/... ./orchestration/... ./skills/... ./tasks/... ./tests/architecture/... ./tests/contract/... ./web/...

.PHONY: setup generate generate-check build test test-go test-python test-web test-policy-sidecar test-tool-layer tools-check lint e2e install-check demo up down stack-status stack-logs home-start home-restart home-accept home-routes sim-start sim-restart sim-status sim-logs sim-stop gazebo-house-start gazebo-house-restart gazebo-house-status gazebo-house-logs gazebo-house-stop sim2real-check deploy-robot-pi production-check fleet-build edge-orin-build fleet-cloud fleet-up fleet-sim fleet-demo fleet-handoff policy-handoff policy-faults fleet-chaos robocasa-install robocasa-install-full robocasa-smoke robocasa-web-assets robocasa-fleet robocasa-handoff robocasa-demo test-robocasa-e2e test-robocasa-faults robocasa-acceptance robocasa-acceptance-candidate robocasa-acceptance-promote

setup:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/pip install -e '.[dev,visual,mcp,context-research]'
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
	.venv/bin/ruff check robot/gateway robot/mcp robot/ros2_ws sim policy scripts tests examples/robots orchestration/eval
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

.PHONY: gazebo-build
gazebo-build:
	.venv/bin/python scripts/build_gazebo_image.py

.PHONY: rgbd-start rgbd-restart
# State the scene explicitly. Without it the lifecycle script reuses whatever
# scene was recorded last, so a plain `make rgbd-start` could reopen the
# four-room home scene, which commissions no tabletop objects, and every
# documented tabletop task would then fail grounding.
rgbd-start: build
	bash scripts/sim-stack.sh start --perception rgbd --scene tabletop

rgbd-restart: build
	bash scripts/sim-stack.sh restart --perception rgbd --scene tabletop

.PHONY: nl-eval
# Score the natural-language path against the case set. Point it at any
# OpenAI-compatible endpoint to compare a candidate model with the baseline:
#   make nl-eval
#   make nl-eval MODEL=my-finetune BASE_URL=http://127.0.0.1:8000/v1 API_KEY=none
# Exit 1 means cases failed; exit 2 means the run could not be completed (for
# example the endpoint was unreachable), which is not the same finding.
nl-eval:
	go build -o bin/nl-eval ./cmd/nl-eval
	./bin/nl-eval -provider "$(if $(MODEL),openai,deterministic)" \
		-base-url "$(BASE_URL)" -api-key "$(API_KEY)" -model "$(MODEL)" -v

.PHONY: home-start home-restart home-accept home-routes home-furnished
# `home-furnished` starts the decorated household demo in an isolated namespace.
# `home-accept` requires manipulation capabilities; it is not a scene-load smoke test.
home-start: build
	bash scripts/sim-stack.sh start --perception rgbd --scene home_task

# The documented console URL is http://127.0.0.1:8897/. sim-stack.sh defaults the
# agent port to 8787, so the ports have to be passed here or the README sends a
# first-time reader to a port nothing listens on. One canonical port, stated once.
home-furnished: build
	bash scripts/furnished-home-demo.sh start --sim-port 50161 --agent-port 8897

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

# Cross-compiled Orin NX binaries. Actual CUDA/model/runtime compatibility is
# validated only on the target device; these are architecture/build checks.
edge-orin-build:
	mkdir -p bin/orin-arm64
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o bin/orin-arm64/local-agent ./cmd/local-agent
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o bin/orin-arm64/edge-worker ./cmd/edge-worker

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

# 输出默认使用独立目录，保留每次物理实验的原始证据。
GVF_OUTPUT ?= artifacts/grounded-verification/run-$(shell date +%Y%m%d-%H%M%S)
GVF_ARGS ?=
.PHONY: gvf-demo gvf-experiment gvf-replay test-gvf
gvf-demo:
	bash scripts/run_grounded_experiments.sh --demo --output "$(GVF_OUTPUT)"

gvf-experiment:
	bash scripts/run_grounded_experiments.sh --output "$(GVF_OUTPUT)" $(GVF_ARGS)

gvf-replay:
	.venv/bin/python scripts/grounded_experiment.py --replay --output "$(GVF_OUTPUT)"

test-gvf:
	.venv/bin/pytest -q robot/gateway/tests/test_grounded_verification.py robot/gateway/tests/test_grounded_experiment.py robot/gateway/tests/test_gazebo_backend.py
	go test ./core/closedloop ./edge/agent ./tasks ./orchestration

# Context quality can be tested offline; live evaluation always uses a new directory.
CONTEXT_OUTPUT ?= artifacts/agent-context-eval/run-$(shell date +%Y%m%d-%H%M%S)
CONTEXT_RUN ?= artifacts/agent-context-eval/run-v2
CONTEXT_ARGS ?=
.PHONY: test-agent-context eval-agent-context eval-agent-context-episodes eval-agent-context-replay eval-agent-context-report
test-agent-context:
	go test ./core/agentcontext ./internal/actionloop ./tasks ./agentruntime ./cmd/local-agent
	.venv/bin/pytest -q tests/eval/test_agent_context.py

eval-agent-context:
	.venv/bin/python scripts/evaluate_agent_context.py --output "$(CONTEXT_OUTPUT)" --phase all $(CONTEXT_ARGS)

eval-agent-context-episodes:
	.venv/bin/python scripts/evaluate_agent_episodes.py --output "$(CONTEXT_RUN)" $(CONTEXT_ARGS)

eval-agent-context-replay:
	.venv/bin/python scripts/evaluate_agent_context.py --output "$(CONTEXT_RUN)" --phase replay
	.venv/bin/python scripts/evaluate_agent_episodes.py --output "$(CONTEXT_RUN)" --replay

eval-agent-context-report:
	.venv/bin/python scripts/report_agent_context.py --source "$(CONTEXT_RUN)" $(CONTEXT_ARGS)

STAGE_RUN ?= artifacts/agent-context-eval/stage-routing-v1/optimized-run
STAGE_CONFIRM ?= artifacts/agent-context-eval/stage-routing-v1/confirmation

FACTORIAL_RUN ?= artifacts/agent-context-eval/factorial-v1/run-v3
.PHONY: test-agent-factorial eval-agent-factorial-replay eval-agent-factorial-formal eval-agent-factorial-audit eval-agent-factorial-report
test-agent-factorial:
	go test ./core/agentcontext ./tasks ./internal/actionloop ./agentruntime ./agent ./orchestration
	.venv/bin/pytest -q tests/eval/test_agent_context.py tests/eval/test_agent_stages.py tests/eval/test_agent_factorial.py

eval-agent-factorial-replay:
	.venv/bin/python scripts/evaluate_agent_factorial.py --output "$(FACTORIAL_RUN)" --phase replay
	.venv/bin/python scripts/confirm_agent_factorial.py --source "$(FACTORIAL_RUN)" --replay
	.venv/bin/python scripts/evaluate_agent_structure.py --source "$(FACTORIAL_RUN)" --replay
	.venv/bin/python scripts/evaluate_agent_contract.py --source "$(FACTORIAL_RUN)" --replay
	.venv/bin/python scripts/release_agent_factorial.py --source "$(FACTORIAL_RUN)"

eval-agent-factorial-formal:
	.venv/bin/python scripts/verify_agent_context_formal.py --source "$(FACTORIAL_RUN)"

eval-agent-factorial-audit:
	.venv/bin/python scripts/audit_agent_factorial.py --source "$(FACTORIAL_RUN)"

eval-agent-factorial-report:
	.venv/bin/python scripts/report_agent_factorial.py --source "$(FACTORIAL_RUN)"
.PHONY: test-agent-stages eval-agent-stages-replay eval-agent-stages-report
test-agent-stages:
	go test ./core/agentcontext ./internal/actionloop ./agent ./orchestration ./tasks ./agentruntime
	.venv/bin/pytest -q tests/eval/test_agent_context.py tests/eval/test_agent_stages.py

eval-agent-stages-replay:
	.venv/bin/python scripts/evaluate_agent_stages.py --output "$(STAGE_RUN)" --phase replay
	.venv/bin/python scripts/confirm_agent_stage_policy.py --source "$(STAGE_RUN)" --output "$(STAGE_CONFIRM)" --replay

eval-agent-stages-report:
	.venv/bin/python scripts/report_agent_stages.py --source "$(STAGE_RUN)"

# Unified offline evidence views; no model, simulator or robot is called.
SYSTEM_EVAL_OUTPUT ?= artifacts/agent-system-eval/run-$(shell date +%Y%m%d-%H%M%S)
.PHONY: test-agent-system eval-agent-system-demo
test-agent-system:
	.venv/bin/pytest -q tests/eval/test_agent_system.py

eval-agent-system-demo:
	.venv/bin/python scripts/evaluate_agent_system.py demo --output "$(SYSTEM_EVAL_OUTPUT)"

.PHONY: gazebo-accept mujoco-start
GAZEBO_ACCEPT_OUTPUT ?= artifacts/acceptance/gazebo-$(shell date +%Y%m%d-%H%M%S)
gazebo-accept:
	.venv/bin/python scripts/evaluate_gazebo_runtime.py --runtime 127.0.0.1:50051 --output "$(GAZEBO_ACCEPT_OUTPUT)" $(GAZEBO_ACCEPT_ARGS)

mujoco-start: build
	bash scripts/sim-stack.sh start --engine mujoco --perception rgbd --scene tabletop
