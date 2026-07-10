# Run all ZT-IIoT prototype experiments on Windows PowerShell.
# Execute from the repository root.

$ErrorActionPreference = "Stop"

Write-Host "Cleaning old containers and results..."
docker compose down --remove-orphans
Remove-Item .\results\* -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path .\results | Out-Null

Write-Host "Building fresh Docker images..."
# Build all Python-based services, including loadgen. This avoids reusing an older loadgen image
# that may not contain newly added experiment scripts.
docker compose build --no-cache modbus-server opcua-server modbus-pep mqtt-pep opcua-pep loadgen

Write-Host "Starting services..."
docker compose up -d modbus-server mqtt-broker opcua-server opa modbus-pep mqtt-pep opcua-pep

Write-Host "Health checks..."
curl.exe http://localhost:8080/health
curl.exe http://localhost:8081/health
curl.exe http://localhost:8082/health
curl.exe http://localhost:8181/health

Write-Host "Running Modbus/TCP experiments..."
docker compose run --rm loadgen python loadgen/baseline_modbus.py --n 1000
docker compose run --rm loadgen python loadgen/benchmark_pep.py --n 1000 --clear-cache
docker compose run --rm loadgen python loadgen/pep_tests.py

docker compose start opa
curl.exe -X POST http://localhost:8080/cache/clear
docker compose run --rm loadgen python loadgen/prime_outage_cache.py --protocol modbus
docker compose stop opa
docker compose run --rm --no-deps loadgen python loadgen/pdp_outage_test.py
docker compose start opa

Write-Host "Running MQTT experiments..."
docker compose run --rm loadgen python loadgen/baseline_mqtt.py --n 1000
docker compose run --rm loadgen python loadgen/benchmark_mqtt_pep.py --n 1000 --clear-cache
docker compose run --rm loadgen python loadgen/mqtt_tests.py

docker compose start opa
curl.exe -X POST http://localhost:8081/cache/clear
docker compose run --rm loadgen python loadgen/prime_outage_cache.py --protocol mqtt
docker compose stop opa
docker compose run --rm --no-deps loadgen python loadgen/mqtt_pdp_outage_test.py
docker compose start opa

Write-Host "Running OPC UA experiments..."
docker compose run --rm loadgen python loadgen/baseline_opcua.py --n 1000
docker compose run --rm loadgen python loadgen/benchmark_opcua_pep.py --n 1000 --clear-cache
docker compose run --rm loadgen python loadgen/opcua_tests.py

docker compose start opa
curl.exe -X POST http://localhost:8082/cache/clear
docker compose run --rm loadgen python loadgen/prime_outage_cache.py --protocol opcua
docker compose stop opa
docker compose run --rm --no-deps loadgen python loadgen/opcua_pdp_outage_test.py
docker compose start opa

Write-Host "Summarizing results..."
docker compose run --rm loadgen python loadgen/summarize_results.py `
  /app/results/baseline_modbus.csv `
  /app/results/pep_benchmark.csv `
  /app/results/baseline_mqtt.csv `
  /app/results/mqtt_pep_benchmark.csv `
  /app/results/baseline_opcua.csv `
  /app/results/opcua_pep_benchmark.csv `
  /app/results/pep_metrics.csv

Write-Host "Done. Results are in .\results\"
