<#
.SYNOPSIS
  Deploy a .NET application to IIS across a set of Windows hosts via WinRM.
.DESCRIPTION
  Consumes a per-deployable JSON vars file (same contract as the Ansible path).
  Per host it: takes the app pool offline, backs up current content, swaps in the
  new artifact, applies app-pool environment variables + config files, restarts
  the pool, and health-checks. On failure it restores the backup on that host
  and stops the rollout (rolling honoured via -Strategy).
.NOTES
  Requires the IISAdministration module on targets. Secrets arrive as process
  env vars (injected by Jenkins withCredentials) and are passed through.
#>
param(
  [Parameter(Mandatory=$true)][string]$VarsFile,   # JSON describing the deployable
  [Parameter(Mandatory=$true)][string]$ArtifactLocal,
  [Parameter(Mandatory=$true)][string]$Inventory,  # comma-separated hosts (resolved by caller)
  [string]$Strategy = "all-at-once"
)
$ErrorActionPreference = "Stop"
$d = Get-Content $VarsFile -Raw | ConvertFrom-Json
$hosts = $Inventory -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$appName   = $d.deployPath.Split('/')[-1].Split('\')[-1]
$sitePath  = $d.deployPath          # physical path of the site/app on the target
$poolName  = $d.appPool ?? $appName

Write-Host "Deploying $($d.id) v$($d.version) to [$($hosts -join ', ')] (strategy=$Strategy)"

function Deploy-One {
  param($h)
  Write-Host ">>> $h"
  $session = New-PSSession -ComputerName $h -ErrorAction Stop
  try {
    # ship the artifact to the target
    $remoteTmp = "C:\deploy\_incoming\$appName-$($d.version)"
    Invoke-Command -Session $session -ScriptBlock { param($p) New-Item -ItemType Directory -Force -Path (Split-Path $p) | Out-Null } -ArgumentList $remoteTmp
    Copy-Item -Path $ArtifactLocal -Destination $remoteTmp -ToSession $session -Force

    Invoke-Command -Session $session -ArgumentList $appName,$sitePath,$poolName,$remoteTmp,$d,$env:APP_SECRETS_JSON -ScriptBlock {
      param($appName,$sitePath,$poolName,$remoteTmp,$d,$secretsJson)
      Import-Module IISAdministration -ErrorAction Stop
      $ts = Get-Date -Format "yyyyMMddHHmmss"
      $backup = "C:\deploy\_backup\$appName-$ts"

      # 1) offline + backup
      Stop-WebAppPool -Name $poolName -ErrorAction SilentlyContinue
      Start-Sleep -Seconds 3
      if (Test-Path $sitePath) {
        New-Item -ItemType Directory -Force -Path $backup | Out-Null
        Copy-Item "$sitePath\*" $backup -Recurse -Force -ErrorAction SilentlyContinue
      }

      # 2) swap content (robocopy mirror from incoming)
      New-Item -ItemType Directory -Force -Path $sitePath | Out-Null
      robocopy $remoteTmp $sitePath /MIR /NFL /NDL /NP | Out-Null

      # 3) app-pool environment variables (config.environment) + secrets
      $envMap = @{}
      if ($d.config -and $d.config.environment) {
        $d.config.environment.PSObject.Properties | ForEach-Object { $envMap[$_.Name] = [string]$_.Value }
      }
      if ($secretsJson) {
        ($secretsJson | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $envMap[$_.Name] = [string]$_.Value }
      }
      foreach ($k in $envMap.Keys) {
        # environmentVariables collection on the app pool (IIS 10+/hostable web core)
        try {
          Set-WebConfigurationProperty -PSPath "MACHINE/WEBROOT/APPHOST" `
            -Filter "system.applicationHost/applicationPools/add[@name='$poolName']/environmentVariables" `
            -Name "." -Value @{name=$k; value=$envMap[$k]} -ErrorAction SilentlyContinue
        } catch { }
      }

      # 4) config files (config.files)
      if ($d.config -and $d.config.files) {
        foreach ($f in $d.config.files) {
          Copy-Item -Path $f.source -Destination $f.destination -Force -ErrorAction SilentlyContinue
        }
      }

      # 5) back online
      Start-WebAppPool -Name $poolName
      return $backup
    }

    # 6) health check
    if ($d.healthCheck.enabled) {
      $scheme = if ($d.healthCheck.protocol) { $d.healthCheck.protocol } else { "http" }
      $port   = if ($d.healthCheck.port) { $d.healthCheck.port } else { 80 }
      $url    = "$scheme`://$h`:$port$($d.healthCheck.endpoint)"
      $ok = $false
      $retries = if ($d.healthCheck.retries) { $d.healthCheck.retries } else { 12 }
      $delay   = if ($d.healthCheck.delaySeconds) { $d.healthCheck.delaySeconds } else { 10 }
      for ($i=0; $i -lt $retries; $i++) {
        try {
          $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
          if ($r.StatusCode -eq ($d.healthCheck.expectedStatus ?? 200)) { $ok = $true; break }
        } catch { }
        Start-Sleep -Seconds $delay
      }
      if (-not $ok) {
        Write-Warning "Health check failed on $h - restoring backup"
        Invoke-Command -Session $session -ArgumentList $sitePath,$poolName -ScriptBlock {
          param($sitePath,$poolName)
          $latest = Get-ChildItem "C:\deploy\_backup" -Directory | Sort-Object LastWriteTime -Desc | Select-Object -First 1
          if ($latest) { robocopy $latest.FullName $sitePath /MIR /NFL /NDL /NP | Out-Null }
          Restart-WebAppPool -Name $poolName
        }
        throw "Deployment failed health check on $h (backup restored)."
      }
    }
    Write-Host "    OK: $h"
  }
  finally { if ($session) { Remove-PSSession $session } }
}

foreach ($h in $hosts) {
  Deploy-One -h $h
  if ($Strategy -eq "rolling") { Start-Sleep -Seconds 10 }   # gap between hosts
}
Write-Host "IIS deploy complete for $($d.id)."
