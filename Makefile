PYTHON ?= python3

.PHONY: setup test api-status api-update harness-setup harness-update compose-config compose-build-config

setup:
	./scripts/bootstrap_api_management.sh
	./scripts/bootstrap_agent_harness.sh

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests

api-status:
	PYTHONPATH=src $(PYTHON) -m omni_hub.cli api-management-status

api-update:
	./scripts/update_api_management.sh

harness-setup:
	./scripts/bootstrap_agent_harness.sh

harness-update:
	./scripts/update_agent_harness.sh

compose-config:
	docker compose --env-file api-management/env.example -f api-management/compose.yml config

compose-build-config:
	docker compose --env-file api-management/env.example -f api-management/compose.yml -f api-management/compose.build.yml config
