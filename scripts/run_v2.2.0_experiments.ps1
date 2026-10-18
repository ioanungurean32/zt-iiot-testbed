# v2.2.0 experiments.
#
# Re-runs only the measurements affected by the v2.2.0 corrections and adds new
# ones; every other result file from v2.1.0 is left untouched.
#
#   (1)   cold (all-miss) latency with disjoint warm-up/measured keys
#         (HTTP pep + matched passthrough, three protocols) and the controlled
#         hit-ratio profiles, which shared the same key-overlap defect
#   (2)   PDP-outage recovery measured over repeated cycles
#         (scripts/pdp_recovery_cycles.ps1) and the partition variant of the
#         outage lifecycle re-run with the corrected OPA reconnection
#   (3)   miss-heavy (cold) and write-heavy (mixed) saturation sweeps, the PDP
#         capacity measured alone, and docker stats of OPA/PEP during the sweeps
#
# The affected v2.1.0 files are copied to results\results_v2.1.0\ first (if not already there).
# Execute from the repository root with the stack up and keys generated:
#   docker compose up -d
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_v2.2.0_experiments.ps1
# Expect roughly 45-60 minutes.

$ErrorActionPreference = "Continue"
$project = "zt_iiot_testbed"
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

Write-Host "== Preserve v2.1.0 results =="
$old = Join-Path $results "results_v2.1.0"
New-Item -ItemType Directory -Force -Path $old | Out-Null
$superseded = @(
  "raw_modbus_pep_cold_c1.csv", "raw_mqtt_pep_cold_c1.csv", "raw_opcua_pep_cold_c1.csv",
  "raw_modbus_passthrough_cold_c1.csv", "raw_mqtt_passthrough_cold_c1.csv", "raw_opcua_passthrough_cold_c1.csv",
  "raw_modbus_pep_hitratio0.5_c1.csv", "raw_modbus_pep_hitratio0.9_c1.csv",
  "summary_stats.csv", "security_overhead.csv", "pdp_outage_lifecycle.csv"
)
foreach ($f in $superseded) {
  $src = Join-Path $results $f
  $dst = Join-Path $old $f
  if ((Test-Path $src) -and -not (Test-Path $dst)) { Copy-Item $src $dst }
}

Write-Host "== Cold (pure-miss) matrix: passthrough / pep, disjoint keys =="
foreach ($proto in @("modbus", "mqtt", "opcua")) {
  foreach ($target in @("passthrough", "pep")) {
    Write-Host "-- $proto $target cold"
    $hargs = @("loadgen/harness.py", "--protocol", $proto, "--target", $target, "--profile", "cold",
               "--n", $N, "--warmup", $WARMUP, "--trials", $TRIALS)
    if ($target -eq "pep") { $hargs += "--clear-cache" }
    docker compose run --rm loadgen python @hargs
  }
}

Write-Host "== Controlled hit-ratio profiles (Modbus PEP) =="
foreach ($profile in @("hitratio:0.5", "hitratio:0.9")) {
  docker compose run --rm loadgen python loadgen/harness.py --protocol modbus --target pep --profile $profile `
    --n $N --warmup $WARMUP --trials $TRIALS --clear-cache
}

Write-Host "== Latency / overhead analysis =="
docker compose run --rm loadgen python loadgen/analyze.py

Write-Host "== Saturation under miss-heavy and write-heavy load =="
$ids = docker compose ps -q opa modbus-pep mqtt-pep opcua-pep
$statsFile = Join-Path $results "docker_stats_saturation.csv"
"timestamp,container,cpu_perc,mem_usage" | Out-File -FilePath $statsFile -Encoding ascii
$sampler = Start-Job -ArgumentList $statsFile, $ids -ScriptBlock {
  param($file, $ids)
  while ($true) {
    $ts = (Get-Date).ToString("o")
    docker stats --no-stream --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}}" @ids |
      ForEach-Object { "$ts,$_" } | Out-File -FilePath $file -Append -Encoding ascii
  }
}
$sweeps = @(
  @{ proto = "modbus"; profile = "cold" },
  @{ proto = "mqtt";   profile = "cold" },
  @{ proto = "opcua";  profile = "cold" },
  @{ proto = "modbus"; profile = "mixed" }
)
foreach ($s in $sweeps) {
  Write-Host "-- saturation $($s.proto) $($s.profile)"
  "$((Get-Date).ToString('o')),MARK,$($s.proto)-$($s.profile)-start," | Out-File -FilePath $statsFile -Append -Encoding ascii
  docker compose run --rm loadgen python loadgen/saturation_sweep.py --protocol $s.proto --profile $s.profile `
    --levels 1,2,5,10,20,50 --n 500 --warmup 50
}

Write-Host "== PDP capacity alone (read-only and 50% writes) =="
"$((Get-Date).ToString('o')),MARK,pdp-capacity-start," | Out-File -FilePath $statsFile -Append -Encoding ascii
docker compose run --rm loadgen python loadgen/pdp_capacity.py --levels 1,2,5,10,20,50 --n 2000
docker compose run --rm loadgen python loadgen/pdp_capacity.py --levels 1,2,5,10,20,50 --n 2000 --write-share 0.5
Stop-Job $sampler | Out-Null
Remove-Job $sampler -Force | Out-Null

Write-Host "== Outage lifecycle, partition variant with corrected reconnection =="
$lifecycle = Join-Path $results "pdp_outage_lifecycle.csv"
if (Test-Path $lifecycle) {
  # Keep the container-shutdown rows, replace the partition rows.
  $keep = Import-Csv $lifecycle | Where-Object { $_.variant -ne "partition" }
  $keep | Export-Csv -Path $lifecycle -NoTypeInformation
}
$net = "${project}_default"
$opa = docker compose ps -q opa
curl.exe -s -X POST http://localhost:8080/cache/clear | Out-Null
docker compose run --rm -e OUTAGE_VARIANT=partition loadgen python loadgen/pdp_outage_lifecycle.py --phase prime
docker network disconnect $net $opa
docker compose run --rm --no-deps -e OUTAGE_VARIANT=partition loadgen python loadgen/pdp_outage_lifecycle.py --phase outage-seq --reps 30
docker network connect --alias opa $net $opa
Start-Sleep -Seconds 5
docker compose run --rm -e OUTAGE_VARIANT=partition loadgen python loadgen/pdp_outage_lifecycle.py --phase recovery

Write-Host "== Recovery over repeated outage cycles =="
& (Join-Path $root "scripts\pdp_recovery_cycles.ps1") -Cycles 10 -TimeoutSec 60

Write-Host "== Done. New and updated files: =="
Get-ChildItem $results -File | Where-Object { $_.LastWriteTime -gt (Get-Date).AddHours(-3) } |
  Select-Object Name, Length, LastWriteTime
