# Default to the project's conda env interpreter (python 3.12) so `make test`
# etc. work without activating anything. Override with `make PYTHON=python3.x`.
# Falls back to the conda-env path if it exists, else plain python3.
PYTHON ?= $(shell test -x $(HOME)/opt/anaconda3/envs/omni-hub/bin/python && echo $(HOME)/opt/anaconda3/envs/omni-hub/bin/python || echo python3)
PYTHON_ABS := $(shell command -v $(PYTHON) 2>/dev/null)

.PHONY: setup test api-status api-update harness-setup harness-update harness-status harness-add-pending harness-ensemble compose-config compose-build-config schedule-install schedule-install-dry schedule-uninstall worker-python worker-claude worker-codex check-python

# Refuse to run the launchd installer (or test runner) against a stale
# Python 3.x on PATH; v0.7 worker pool requires 3.12+ (datetime.UTC, etc.).
# Plain `%`-formatting so the check itself parses on Python 3.7+.
check-python:
	@$(PYTHON) -c "import sys; v=sys.version_info; ok=(v.major, v.minor) >= (3, 12); sys.stderr.write('ERROR: PYTHON=$(PYTHON) -> %d.%d; need >= 3.12. Set PYTHON= to a 3.12+ interpreter.\n' % (v.major, v.minor)) if not ok else None; sys.exit(0 if ok else 2)"

setup:
	./scripts/bootstrap_api_management.sh
	./scripts/bootstrap_agent_harness.sh

test: check-python
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests

api-status:
	PYTHONPATH=src $(PYTHON) -m omni_hub.cli api-management-status

api-update:
	./scripts/update_api_management.sh

harness-setup:
	./scripts/bootstrap_agent_harness.sh

harness-update:
	./scripts/update_agent_harness.sh

harness-status:
	@$(PYTHON) -c "import json; m=json.load(open('agent-harness/manifest.json')); \
ready=m.get('forks',[]); pending=m.get('pending_forks',[]); \
print('ready modules (' + str(len(ready)) + '): ' + ', '.join(f['id'] for f in ready)); \
print('pending forks (' + str(len(pending)) + '): ' + (', '.join(f['id'] for f in pending) or '(none)')); \
print(); \
[print('  ' + f['id'].ljust(10) + ' <- ' + f['upstream'] + '\n    ' + f['role']) for f in pending]"

harness-add-pending:
	./scripts/add_pending_harness_forks.sh $(filter-out $@,$(MAKECMDGOALS))

harness-ensemble:
	PYTHONPATH=src $(PYTHON) -m omni_hub.cli harness-ensemble $(filter-out $@,$(MAKECMDGOALS))

compose-config:
	docker compose --env-file api-management/env.example -f api-management/compose.yml config

compose-build-config:
	docker compose --env-file api-management/env.example -f api-management/compose.yml -f api-management/compose.build.yml config

# ---- launchd scheduler ------------------------------------------------------

schedule-install-dry: check-python
	$(PYTHON) scripts/install_launchd.py --dry-run --python $(PYTHON_ABS)

schedule-install: check-python
	$(PYTHON) scripts/install_launchd.py --python $(PYTHON_ABS)

schedule-uninstall:
	$(PYTHON) scripts/uninstall_launchd.py

# ---- workers (drain queue once and exit, useful for manual smoke tests) ----

worker-python:
	PYTHONPATH=src $(PYTHON) -m omni_hub.cli worker --lane python --idle-exit-after-sec 2

worker-claude:
	PYTHONPATH=src $(PYTHON) -m omni_hub.cli worker --lane claude --idle-exit-after-sec 2

worker-codex:
	PYTHONPATH=src $(PYTHON) -m omni_hub.cli worker --lane codex --idle-exit-after-sec 2
