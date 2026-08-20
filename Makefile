.PHONY: build keys up down logs clean tests analyze evaluate

build:
	docker compose build

keys:
	docker compose run --rm loadgen python tools/generate_keys.py

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs --tail=100

clean:
	rm -f results/*.csv

# Correctness and functional tests.
tests:
	docker compose run --rm loadgen python loadgen/cache_invalidation_tests.py
	docker compose run --rm loadgen python loadgen/token_security_tests.py
	docker compose run --rm loadgen python loadgen/pep_tests.py
	docker compose run --rm loadgen python loadgen/mqtt_tests.py
	docker compose run --rm loadgen python loadgen/opcua_tests.py

# Aggregate raw harness data into statistics and isolated overhead.
analyze:
	docker compose run --rm loadgen python loadgen/analyze.py

# Full evaluation (matched baselines, workload, saturation, outage lifecycle).
# On Windows use: powershell -ExecutionPolicy Bypass -File .\scripts\run_full_evaluation.ps1
evaluate: build keys up
	docker compose run --rm loadgen python loadgen/harness.py --protocol modbus --target direct      --profile steady --n 1000 --warmup 100 --trials 10
	docker compose run --rm loadgen python loadgen/harness.py --protocol modbus --target passthrough --profile steady --n 1000 --warmup 100 --trials 10
	docker compose run --rm loadgen python loadgen/harness.py --protocol modbus --target pep         --profile steady --n 1000 --warmup 100 --trials 10 --clear-cache
	docker compose run --rm loadgen python loadgen/analyze.py
