PYTHON ?= python3.11

.PHONY: setup generate generate-check test test-go test-python lint e2e install-check demo sim2real-check deploy-robot-pi production-check

setup:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/pip install -e '.[dev]'
	go mod download

generate:
	bash scripts/generate-proto.sh

generate-check: generate
	git diff --exit-code -- gen/go python/tangying_robot_proto

test-go:
	go test ./...

test-python:
	.venv/bin/pytest -q

test: test-go test-python

lint:
	gofmt -l $$(find . -name '*.go' -not -path './gen/*') | tee /tmp/tangying-gofmt.out
	test ! -s /tmp/tangying-gofmt.out
	.venv/bin/ruff check .

e2e:
	.venv/bin/pytest tests/e2e -q

install-check:
	bash -n install.sh scripts/install/*.sh scripts/demo.sh scripts/robot-pi-quick-deploy.sh scripts/robot-pi-preflight.sh
	.venv/bin/pytest tests/install -q

demo:
	bash scripts/demo.sh

sim2real-check:
	.venv/bin/pytest tests/e2e -q
	.venv/bin/python scripts/run_simulation_acceptance.py --episodes 30 --seed 20260817

deploy-robot-pi:
	bash scripts/robot-pi-quick-deploy.sh

production-check:
	sudo robot-agent production-check robot-pi
