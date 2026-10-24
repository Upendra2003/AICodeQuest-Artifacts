PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
COMPOSE := docker compose -f infra/docker-compose.yml

.PHONY: help venv up down test lint judge0-conf demo-phase-0 demo-phase-1 demo-phase-2 demo-phase-3 demo-phase-4 demo-phase-5 demo-phase-6 demo-phase-8 demo-phase-9 demo-phase-10 demo-phase-11 demo-phase-13 demo-phase-30 architecture-doc reconcile-leaderboard check-alerts export-study-data backup restore report

help:
	@echo "Targets: venv, up, down, test, lint, judge0-conf, demo-phase-0..11, demo-phase-13, reconcile-leaderboard, check-alerts, export-study-data, backup, restore, report"

# Not phase-0 deliverable content itself, just the mechanism to get a
# runnable environment before the other targets are meaningful.
# Dev Redis is published on 16379, not 6379 (a real port collision on the
# shared lab machine -- see reports/phase-13-review.md). Database 7 keeps
# the test suite's queues away from the running dev worker's.
TEST_REDIS_URL ?= redis://localhost:16379/7

venv:
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements-dev.txt

# Regenerates infra/judge0/judge0.conf from the template + current
# config/params.yaml so Judge0's limits never drift from our own.
judge0-conf:
	$(PYTHON) infra/judge0/render_conf.py

# `worker` is in this list deliberately. Without it nothing consumes the
# `generation` queue, so every Developer-gate turn is generated inline in
# the request that asked for it -- the difference between a ~1.4s turn and
# a ~50s one (reports/phase-24-review.md). `--force-recreate` on the
# worker because `up -d` alone leaves an ALREADY-RUNNING container on its
# old definition: that is exactly how the worker ran for days without
# OLLAMA_BASE_URL, silently degrading every turn to a stand-in exercise.
up: judge0-conf
	$(COMPOSE) up -d mongo redis judge0-db judge0-redis judge0-server judge0-workers
	$(COMPOSE) up -d --force-recreate worker

down:
	$(COMPOSE) down

# The RQ worker imports the backend at process start, so it keeps running
# whatever the code looked like WHEN IT STARTED. Editing anything under
# backend/app/ leaves the worker on the old version -- and because it
# still consumes the queue, the foreground waits out the full
# prefetch.inline_wait_ms for jobs the stale worker fails or generates
# differently. This has cost real debugging time twice; run it after
# touching backend code.
worker-restart:
	$(COMPOSE) restart worker

# REDIS_URL points at the dev Redis's database 7, isolated from the app's
# default 0. Without it the dev `worker` container -- which is listening
# on the same Redis -- picks up the tests' own RQ jobs, runs them against
# ITS database rather than the test's mongomock one, and about eleven
# tests fail with errors that have nothing to do with the code under test.
test:
	cd backend && REDIS_URL=$(TEST_REDIS_URL) ../$(PYTHON) -m pytest tests -q

lint:
	$(PYTHON) -m flake8 backend/app backend/scripts backend/tests

demo-phase-0: up
	cd backend && ../$(PYTHON) scripts/demo_phase_0.py

demo-phase-1: up
	cd backend && ../$(PYTHON) scripts/demo_phase_1.py

demo-phase-2: up
	cd backend && ../$(PYTHON) scripts/demo_phase_2.py

demo-phase-3: up
	cd backend && ../$(PYTHON) scripts/demo_phase_3.py

demo-phase-4: up
	cd backend && ../$(PYTHON) scripts/demo_phase_4.py

demo-phase-5: up
	cd backend && ../$(PYTHON) scripts/demo_phase_5.py

# Runs its own in-process RQ worker (SimpleWorker, against mongomock) --
# does NOT need the docker-compose `worker` service, which `up` does not
# start (only mongo/redis/judge0-*). Starting that container alongside
# this demo would just have two workers racing over the same Redis
# queues for no benefit (see reports/phase-6-review.md).
demo-phase-6: up
	cd backend && ../$(PYTHON) scripts/demo_phase_6.py

# Scores against the REAL local Ollama model (not a mock) -- takes a few
# minutes (~20 live calls: 2 question generations + 2 NFRs x 3 sample
# Phase 30 replaced the Lead gate's free-text-and-rubric design with a
# multiple-choice item plus an optional human-verification form, and
# demo_phase_7.py went with it. This runs the REAL local model (not a
# mock) and prints each generated item beside its QC verdict, because
# the phase's whole claim is that we stopped assuming LLM-authored
# assessment content is sound. Requires Ollama running locally with the
# configured model (llm.model in config/params.yaml). Expect ~20-30s per
# item and expect some items to fail QC -- that is the measurement.
demo-phase-30: up
	cd backend && ../$(PYTHON) scripts/demo_phase_30.py

# Regenerates docs/ARCHITECTURE.docx from docs/ARCHITECTURE.md. The
# markdown is the source of truth; the .docx is disposable output. Needs
# no services -- it only reads a file.
architecture-doc:
	$(PYTHON) backend/scripts/generate_architecture_doc.py

# Needs `cd extension && npm install` once first. The Playwright e2e
# suite needs a real (or virtual, e.g. Xvfb) display -- Chrome does not
# load unpacked MV3 extensions in headless mode at all. On a normal
# Debian/Ubuntu machine or GitHub Actions' ubuntu-latest, `apt install
# xvfb` provides `xvfb-run`, which extension/package.json's test:e2e
# script uses automatically. See docs/CHROME-EXTENSION-PRIMER.md and
# reports/phase-8-review.md open questions for what this sandbox needed
# instead (no sudo/apt access).
demo-phase-8:
	cd backend && ../$(PYTHON) scripts/demo_phase_8.py

# Same display requirement as demo-phase-8. Also spins up its own real
# listening Flask server (backend/scripts/e2e_server.py) via Playwright's
# webServer config -- no `up`/docker-compose infra needed, this phase's
# e2e tests use mongomock + a ScriptedLLM, not real Mongo/Judge0/Ollama.
demo-phase-9:
	cd backend && ../$(PYTHON) scripts/demo_phase_9.py

# No display, no docker-compose infra needed -- this phase is backend-
# only (a GitHub App webhook receiver), tested entirely against a mocked
# GitHub API (`responses`) and mongomock. See docs/GITHUB-APP-SETUP.md
# for why nothing here has been exercised against a real GitHub App.
demo-phase-10:
	cd backend && ../$(PYTHON) scripts/demo_phase_10.py

# Needs real Mongo + Redis (`up`) -- the leaderboard aggregate is
# maintained via an RQ "maintenance"-queue job on every gate completion,
# and this demo drives that for real.
demo-phase-11: up
	cd backend && ../$(PYTHON) scripts/demo_phase_11.py

# Meant to run nightly via cron in a real deployment (see the script's
# own docstring for a crontab line) -- exposed here for manual runs/
# testing. Exits non-zero if drift was found, so a cron wrapper can alert.
reconcile-leaderboard: up
	cd backend && ../$(PYTHON) scripts/reconcile_leaderboard.py

# Phase 13: meant to run every few minutes via cron during the study (see
# the script's own docstring for a crontab line). Exits non-zero on any
# breach so a cron wrapper's own failure-mail can alert even with no
# webhook configured yet.
check-alerts: up
	cd backend && ../$(PYTHON) scripts/check_alerts.py

# Phase 12/13: requires STUDY_PSEUDONYM_SALT in the environment (the
# script refuses to run without it -- see its own docstring).
export-study-data: up
	cd backend && ../$(PYTHON) scripts/export_study_data.py

# Phase 13: mongodump-based backup/restore -- see docs/RUNBOOK.md's
# "restore from backup" section and backend/scripts/backup_mongo.py /
# restore_mongo.py's own docstrings for what BACKUP_DIR defaults to and
# how to point either at a specific archive.
backup: up
	cd backend && ../$(PYTHON) scripts/backup_mongo.py

restore: up
	cd backend && ../$(PYTHON) scripts/restore_mongo.py $(ARCHIVE)

demo-phase-13: up
	cd backend && ../$(PYTHON) scripts/demo_phase_13.py

# Each phase's demo script both runs the demo and writes that phase's
# report; `report` always points at the current (latest) phase.
report: demo-phase-13
