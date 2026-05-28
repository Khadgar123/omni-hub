PYTHON ?= python3

.PHONY: setup test api-status api-update harness-setup harness-update harness-status harness-add-pending harness-ensemble compose-config compose-build-config

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

harness-status:
	@$(PYTHON) -c "import json; m=json.load(open('agent-harness/manifest.json')); \
ready=m.get('forks',[]); pending=m.get('pending_forks',[]); \
print('ready forks (' + str(len(ready)) + '): ' + ', '.join(f['id'] for f in ready)); \
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
