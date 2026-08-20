# Full ZT-IIoT empirical evaluation.
# Regenerates every reported result from isolated experiments with independent
# repetition. Execute from the repository root. Expect ~1-1.5 hours.

$ErrorActionPreference = "Stop"
$project = "zt_iiot_testbed"

$TRIALS = 10
$N = 1000
$WARMUP = 100

Write-Host "== Clean, build, keys, start =="
docker compose down --remove-orphans
Remove-Item .\results\* -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path .\results | Out-Null
docker compose build
New-Item -ItemType Directory -Force -Path .\keys | Out-Null
docker compose run --rm loadgen python tools/generate_keys.py
docker compose up -d
Start-Sleep -Seconds 8

Write-Host "== Health =="
foreach ($p in 8080,8081,8082,8090,8091,8092,8181) { curl.exe -s "http://localhost:$p/health"; Write-Host "" }

Write-Host "== Correctness tests (cache invalidation, token security) =="
docker compose run --rm loadgen python loadgen/cache_invalidation_tests.py
docker compose run --rm loadgen python loadgen/token_security_tests.py

Write-Host "== Transparent native-wire Modbus PEP: functional + non-bypassability =="
docker compose run --rm loadgen python loadgen/modbus_proxy_tests.py

Write-Host "== Functional policy tests =="
docker compose run --rm loadgen python loadgen/pep_tests.py
docker compose run --rm loadgen python loadgen/mqtt_tests.py
docker compose run --rm loadgen python loadgen/opcua_tests.py

Write-Host "== Latency matrix: direct / passthrough / pep =="
$protocols = @("modbus","mqtt","opcua")
foreach ($proto in $protocols) {
  foreach ($target in @("direct","passthrough","pep")) {
    foreach ($profile in @("steady","cold")) {
      Write-Host "-- $proto $target $profile"
      $hargs = @("loadgen/harness.py","--protocol",$proto,"--target",$target,"--profile",$profile,"--n",$N,"--warmup",$WARMUP,"--trials",$TRIALS)
      if ($target -eq "pep") { $hargs += "--clear-cache" }
      docker compose run --rm loadgen python @hargs
    }
  }
}

Write-Host "== Workload-diversity profiles (Modbus PEP) =="
foreach ($profile in @("mixed","hitratio:0.5","hitratio:0.9")) {
  docker compose run --rm loadgen python loadgen/harness.py --protocol modbus --target pep --profile $profile --n $N --warmup $WARMUP --trials $TRIALS --clear-cache
}

Write-Host "== Concurrency / saturation sweep =="
foreach ($proto in $protocols) {
  docker compose run --rm loadgen python loadgen/saturation_sweep.py --protocol $proto --profile steady --levels 1,2,5,10,20,50 --n 500 --warmup 50
}

Write-Host "== Transparent native-wire Modbus latency matrix: direct / native-passthrough / native-pep =="
foreach ($target in @("native-direct","native-passthrough","native-pep")) {
  foreach ($profile in @("steady","cold")) {
    Write-Host "-- modbus $target $profile"
    docker compose run --rm loadgen python loadgen/modbus_native_harness.py --target $target --profile $profile --n $N --warmup $WARMUP --trials $TRIALS
  }
}

Write-Host "== Latency / overhead analysis (HTTP gateways + native-wire proxy) =="
docker compose run --rm loadgen python loadgen/analyze.py

Write-Host "== PDP-outage lifecycle: container shutdown variant =="
docker compose run --rm loadgen python loadgen/pdp_outage_lifecycle.py --phase reset
curl.exe -s -X POST http://localhost:8080/cache/clear | Out-Null
docker compose run --rm loadgen python loadgen/pdp_outage_lifecycle.py --phase prime
docker compose stop opa
docker compose run --rm --no-deps loadgen python loadgen/pdp_outage_lifecycle.py --phase outage-seq --reps 30
docker compose run --rm --no-deps loadgen python loadgen/pdp_outage_lifecycle.py --phase outage-conc --conc 20
docker compose run --rm --no-deps loadgen python loadgen/pdp_outage_lifecycle.py --phase outage-expiry
docker compose start opa
Start-Sleep -Seconds 5
docker compose run --rm loadgen python loadgen/pdp_outage_lifecycle.py --phase recovery

Write-Host "== PDP-outage lifecycle: network-partition variant =="
# Auto-detect the compose network and OPA container so a naming mismatch does
# not abort the run after all other data is already collected.
$net = docker network ls --format "{{.Name}}" | Where-Object { $_ -like "*${project}*" } | Select-Object -First 1
$opa = docker ps --format "{{.Names}}" | Where-Object { $_ -like "*${project}*opa*" } | Select-Object -First 1
try {
  if (-not $net -or -not $opa) { throw "could not detect network ($net) or opa container ($opa)" }
  curl.exe -s -X POST http://localhost:8080/cache/clear | Out-Null
  docker compose run --rm -e OUTAGE_VARIANT=partition loadgen python loadgen/pdp_outage_lifecycle.py --phase prime
  docker network disconnect $net $opa
  docker compose run --rm --no-deps -e OUTAGE_VARIANT=partition loadgen python loadgen/pdp_outage_lifecycle.py --phase outage-seq --reps 30
  docker network connect $net $opa
  Start-Sleep -Seconds 5
  docker compose run --rm -e OUTAGE_VARIANT=partition loadgen python loadgen/pdp_outage_lifecycle.py --phase recovery
} catch {
  Write-Host "Network-partition variant skipped: $_"
  Write-Host "(The container-shutdown outage lifecycle above already completed.)"
}

Write-Host "== Done. Raw data and summaries are in .\results\ =="
Get-ChildItem .\results\ | Select-Object Name, Length
