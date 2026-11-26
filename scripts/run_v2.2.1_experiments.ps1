# v2.2.1 experiments.
#
# v2.2.1 changes only the transparent native-wire Modbus PEP (per-client identity
# bindings and an authenticated, unpublished admin interface). This script
# re-runs only the results produced through that proxy; every other result file
# from v2.2.0 is left untouched:
#
#   (1) the functional, identity-binding, admin-interface and non-bypassability
#       suite of the proxy (results/modbus_proxy_tests.csv)
#   (2) the native-wire latency matrix (native-direct / native-passthrough /
#       native-pep, steady and cold), re-run together so that the native
#       baselines and the native PEP come from the same session
#   (3) analyze.py, which recomputes summary_stats.csv and security_overhead.csv
#       from all raw files (the HTTP-gateway raw files are not re-run)
#
# The affected v2.2.0 files are copied to results\results_v2.2.0\ first (if not
# already there). Execute from the repository root with the stack up:
#   docker compose up -d
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_v2.2.1_experiments.ps1
# Expect roughly 10-15 minutes.

$ErrorActionPreference = "Continue"
$TRIALS = 10
$N = 1000
$WARMUP = 100
$root = (Get-Location).Path
$results = Join-Path $root "results"

Write-Host "== Health =="
foreach ($p in 8080,8081,8082,8090,8091,8092,8181) {
  $h = curl.exe -s --max-time 5 "http://localhost:$p/health"
  if (-not $h) { throw "service on port $p is not answering; run docker compose up -d first" }
}

Write-Host "== Preserve v2.2.0 results =="
$old = Join-Path $results "results_v2.2.0"
New-Item -ItemType Directory -Force -Path $old | Out-Null
$superseded = @(
  "modbus_proxy_tests.csv", "modbus_proxy_metrics.csv",
  "raw_modbus_native-direct_steady_c1.csv", "raw_modbus_native-direct_cold_c1.csv",
  "raw_modbus_native-passthrough_steady_c1.csv", "raw_modbus_native-passthrough_cold_c1.csv",
  "raw_modbus_native-pep_steady_c1.csv", "raw_modbus_native-pep_cold_c1.csv",
  "summary_stats.csv", "security_overhead.csv"
)
foreach ($f in $superseded) {
  $src = Join-Path $results $f
  $dst = Join-Path $old $f
  if ((Test-Path $src) -and -not (Test-Path $dst)) { Copy-Item $src $dst }
}
# The proxy appends to its per-request log; start a fresh one for this run.
Remove-Item (Join-Path $results "modbus_proxy_metrics.csv") -ErrorAction SilentlyContinue
Remove-Item (Join-Path $results "modbus_proxy_admin_audit.csv") -ErrorAction SilentlyContinue

Write-Host "== Rebuild the proxy and loadgen images; key layout and admin secret (key pair kept) =="
docker compose build loadgen modbus-proxy-pep modbus-proxy-gw
docker compose run --rm --no-deps loadgen python tools/generate_keys.py --keep-key-pair
# Recreates the proxies (new image) and every PEP whose key mount changed.
docker compose up -d
Start-Sleep -Seconds 5

Write-Host "== Admin port must not be reachable from the host =="
$hostAdmin = curl.exe -s --max-time 3 "http://localhost:8083/health"
if ($hostAdmin) { Write-Warning "admin port 8083 answered from the host: $hostAdmin" }
else { Write-Host "OK: http://localhost:8083 is not reachable from the host" }

Write-Host "== Transparent native-wire Modbus PEP: functional, identity binding, admin, non-bypassability =="
docker compose run --rm loadgen python loadgen/modbus_proxy_tests.py

Write-Host "== Transparent native-wire Modbus latency matrix: direct / native-passthrough / native-pep =="
foreach ($target in @("native-direct","native-passthrough","native-pep")) {
  foreach ($profile in @("steady","cold")) {
    Write-Host "-- modbus $target $profile"
    docker compose run --rm loadgen python loadgen/modbus_native_harness.py --target $target --profile $profile --n $N --warmup $WARMUP --trials $TRIALS
  }
}

Write-Host "== Latency / overhead analysis (HTTP gateways + native-wire proxy) =="
docker compose run --rm loadgen python loadgen/analyze.py

Write-Host "== Done. Updated: modbus_proxy_tests.csv, raw_modbus_native-*.csv, summary_stats.csv, security_overhead.csv =="
