# PDP-outage recovery over repeated outage cycles.
#
# For each cycle the PDP (OPA) is made unavailable, the fail-closed behaviour
# is checked, the PDP is restored, and the host then probes a protected
# maintenance write through the Modbus HTTP PEP back-to-back until it succeeds
# (HTTP 200). The reported recovery time runs from the moment the restore
# command returns to the first successful protected write, so it measures the
# restoration of protected *write availability*, not only of cached reads.
#
# Fault models:
#   partition  docker network disconnect / connect of the OPA container
#   shutdown   docker compose stop / start opa
#
# v2.1.0 reconnected OPA with a plain `docker network connect`, which drops the
# Compose service alias `opa`; the PEP then cannot resolve its PDP after the
# partition heals and protected writes stay fail-closed indefinitely. This
# script reconnects with `--alias opa` and, unless -SkipLegacyCycle is given,
# first runs one diagnostic cycle with the v2.1.0 reconnection so that the
# difference is recorded in the same CSV (orchestration = legacy_no_alias).
#
# Usage (repository root, stack up, keys generated):
#   powershell -ExecutionPolicy Bypass -File .\scripts\pdp_recovery_cycles.ps1
#   ... -Cycles 10 -TimeoutSec 60 -Variants partition,shutdown

param(
  [int]$Cycles = 10,
  [int]$TimeoutSec = 60,
  [int]$LegacyTimeoutSec = 30,
  [string[]]$Variants = @("partition", "shutdown"),
  [switch]$SkipLegacyCycle
)

# Docker CLI progress goes to stderr; with "Stop", Windows PowerShell 5.1 would
# turn it into terminating errors, so failures are checked explicitly instead.
$ErrorActionPreference = "Continue"
$project = "zt_iiot_testbed"
$pep = "http://localhost:8080"
$out = ".\results\pdp_recovery_cycles.csv"

$net = "${project}_default"
$opa = docker compose ps -q opa
if (-not $opa) { throw "OPA container is not running (docker compose up -d first)" }

# Request bodies go through files: PowerShell 5.1 strips quotes from JSON
# passed as native-command arguments.
$tmp = Join-Path $env:TEMP "zt_recovery"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$readBody = Join-Path $tmp "read.json"
$writeBody = Join-Path $tmp "write.json"
[IO.File]::WriteAllText($readBody, '{"register_address": 40001, "count": 1, "maintenance_window": false}')
[IO.File]::WriteAllText($writeBody, '{"register_address": 40005, "value": 1600, "maintenance_window": true}')

Write-Host "== Minting tokens (off the timed path) =="
$tokRead = docker compose run --rm loadgen python tools/mint_token.py --subject dev_recovery --role operator --risk-score 20 --ttl 7200 | Select-Object -Last 1
$tokRead = "$tokRead".Trim()
$tokWrite = docker compose run --rm loadgen python tools/mint_token.py --subject dev_recovery --role maintenance_engineer --risk-score 20 --ttl 7200 | Select-Object -Last 1
$tokWrite = "$tokWrite".Trim()
if (-not $tokRead -or -not $tokWrite) { throw "token minting failed (generate keys first)" }

function Invoke-Pep([string]$path, [string]$body, [string]$token) {
  $code = & curl.exe -s -o NUL -w "%{http_code}" -X POST `
    -H "Content-Type: application/json" -H "Authorization: Bearer $token" `
    --data-binary "@$body" --max-time 5 "$pep$path"
  return [int]$code
}

function Test-OpaDns {
  $r = docker compose exec -T modbus-pep python -c "import socket;print(socket.gethostbyname('opa'))" 2>$null
  if ($LASTEXITCODE -eq 0 -and $r) { return "resolved:$($r | Select-Object -Last 1)" }
  return "unresolved"
}

function Break-Pdp([string]$variant) {
  if ($variant -eq "partition") { docker network disconnect $net $opa | Out-Null }
  else { docker compose stop opa 2>$null | Out-Null }
}

function Restore-Pdp([string]$variant, [bool]$withAlias) {
  if ($variant -eq "partition") {
    if ($withAlias) { docker network connect --alias opa $net $opa | Out-Null }
    else { docker network connect $net $opa | Out-Null }
  } else {
    docker compose start opa 2>$null | Out-Null
  }
}

function Wait-Recovery([int]$timeout) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $attempts = 0
  while ($sw.Elapsed.TotalSeconds -lt $timeout) {
    $attempts++
    $code = Invoke-Pep "/modbus/write" $writeBody $tokWrite
    if ($code -eq 200) {
      return @{ recovered = $true; ms = [math]::Round($sw.Elapsed.TotalMilliseconds, 1); attempts = $attempts }
    }
    Start-Sleep -Milliseconds 50
  }
  return @{ recovered = $false; ms = [math]::Round($sw.Elapsed.TotalMilliseconds, 1); attempts = $attempts }
}

function Run-Cycle([string]$variant, [int]$cycle, [string]$orchestration, [int]$timeout) {
  # Healthy start: empty cache, then prime a cacheable read decision.
  curl.exe -s -X POST "$pep/cache/clear" | Out-Null
  $prime = Invoke-Pep "/modbus/read" $readBody $tokRead
  $preWrite = Invoke-Pep "/modbus/write" $writeBody $tokWrite

  Break-Pdp $variant
  $outWrite = Invoke-Pep "/modbus/write" $writeBody $tokWrite
  $outRead = Invoke-Pep "/modbus/read" $readBody $tokRead

  $swCmd = [Diagnostics.Stopwatch]::StartNew()
  Restore-Pdp $variant ($orchestration -ne "legacy_no_alias")
  $restoreCmdMs = [math]::Round($swCmd.Elapsed.TotalMilliseconds, 1)

  $rec = Wait-Recovery $timeout
  $dns = Test-OpaDns

  $row = [pscustomobject]@{
    variant = $variant; cycle = $cycle; orchestration = $orchestration
    prime_read_status = $prime; pre_outage_write_status = $preWrite
    outage_write_status = $outWrite; outage_cached_read_status = $outRead
    restore_cmd_ms = $restoreCmdMs; recovered = $rec.recovered
    time_to_first_write_200_ms = $rec.ms; probe_attempts = $rec.attempts
    opa_dns_after = $dns
  }
  $row | Export-Csv -Path $out -NoTypeInformation -Append
  Write-Host ("{0} #{1} [{2}] outage write={3} cached read={4} -> recovered={5} in {6} ms ({7} probes), dns={8}" -f `
    $variant, $cycle, $orchestration, $outWrite, $outRead, $rec.recovered, $rec.ms, $rec.attempts, $dns)

  if (-not $rec.recovered -and $variant -eq "partition") {
    # Repair the v2.1.0 reconnection so that later cycles start healthy.
    docker network disconnect $net $opa | Out-Null
    docker network connect --alias opa $net $opa | Out-Null
  }
  Start-Sleep -Seconds 2
}

Remove-Item $out -ErrorAction SilentlyContinue

if (-not $SkipLegacyCycle -and ($Variants -contains "partition")) {
  Write-Host "== Diagnostic: v2.1.0 reconnection (no service alias) =="
  Run-Cycle "partition" 0 "legacy_no_alias" $LegacyTimeoutSec
}

foreach ($variant in $Variants) {
  Write-Host "== $variant : $Cycles cycles =="
  for ($i = 1; $i -le $Cycles; $i++) {
    Run-Cycle $variant $i "alias_restored" $TimeoutSec
  }
}

Write-Host "== Summary (alias_restored cycles) =="
$rows = Import-Csv $out | Where-Object { $_.orchestration -eq "alias_restored" }
foreach ($variant in $Variants) {
  $v = $rows | Where-Object { $_.variant -eq $variant }
  $ok = @($v | Where-Object { $_.recovered -eq "True" })
  $failClosed = @($v | Where-Object { $_.outage_write_status -eq "403" }).Count
  if ($ok.Count -gt 0) {
    $t = $ok | ForEach-Object { [double]$_.time_to_first_write_200_ms } | Sort-Object
    $mean = ($t | Measure-Object -Average).Average
    Write-Host ("{0}: outage writes fail-closed {1}/{2}; recovered {3}/{2}; time-to-first-write-200 mean={4:N1} ms min={5:N1} max={6:N1}" -f `
      $variant, $failClosed, @($v).Count, $ok.Count, $mean, $t[0], $t[-1])
  } else {
    Write-Host ("{0}: no successful recovery within {1} s" -f $variant, $TimeoutSec)
  }
}
Write-Host "Wrote $out"
