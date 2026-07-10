.PHONY: up down logs clean modbus mqtt opcua summarize

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs --tail=100

clean:
	rm -f results/*.csv

modbus:
	docker compose up --build -d modbus-server opa modbus-pep
	docker compose run --rm loadgen python loadgen/baseline_modbus.py --n 1000
	docker compose run --rm loadgen python loadgen/benchmark_pep.py --n 1000 --clear-cache
	docker compose run --rm loadgen python loadgen/pep_tests.py

mqtt:
	docker compose up --build -d mqtt-broker opa mqtt-pep
	docker compose run --rm loadgen python loadgen/baseline_mqtt.py --n 1000
	docker compose run --rm loadgen python loadgen/benchmark_mqtt_pep.py --n 1000 --clear-cache
	docker compose run --rm loadgen python loadgen/mqtt_tests.py

opcua:
	docker compose up --build -d opcua-server opa opcua-pep
	docker compose run --rm loadgen python loadgen/baseline_opcua.py --n 1000
	docker compose run --rm loadgen python loadgen/benchmark_opcua_pep.py --n 1000 --clear-cache
	docker compose run --rm loadgen python loadgen/opcua_tests.py

summarize:
	docker compose run --rm loadgen python loadgen/summarize_results.py /app/results/baseline_modbus.csv /app/results/pep_benchmark.csv /app/results/baseline_mqtt.csv /app/results/mqtt_pep_benchmark.csv /app/results/baseline_opcua.csv /app/results/opcua_pep_benchmark.csv /app/results/pep_metrics.csv
