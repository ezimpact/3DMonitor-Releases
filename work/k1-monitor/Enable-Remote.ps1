$ErrorActionPreference = 'Stop'
try {
    $tailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (-not (Test-Path -LiteralPath $tailscaleExe)) { throw 'Tailscale is not installed.' }
    $tailscaleInfo = & $tailscaleExe status --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $tailscaleInfo.BackendState -ne 'Running') { throw 'Connect Tailscale on this PC first.' }
    $remoteHost = $tailscaleInfo.Self.DNSName.TrimEnd('.').ToLowerInvariant()
    if ($remoteHost -notmatch '^[a-z0-9-]+\.[a-z0-9-]+\.ts\.net$') { throw 'Tailscale DNS name is unavailable.' }
    $serveState = & $tailscaleExe serve status --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read Tailscale Serve configuration.' }
    if (@($serveState.PSObject.Properties).Count -gt 0) {
        $serveKey = $remoteHost + ':443'
        $webHandler = $serveState.Web.PSObject.Properties[$serveKey].Value
        $rootHandler = $webHandler.Handlers.PSObject.Properties['/'].Value
        $funnelEnabled = @($serveState.AllowFunnel.PSObject.Properties | Where-Object { $_.Value -eq $true }).Count -gt 0
        if ($rootHandler.Proxy -ne 'http://127.0.0.1:8765' -or $funnelEnabled -or @($serveState.Web.PSObject.Properties).Count -ne 1 -or @($serveState.TCP.PSObject.Properties).Count -ne 1) {
            throw 'Another Serve/Funnel configuration exists. It was left unchanged.'
        }
    }
    $baseUrl = 'http://127.0.0.1:8765'
    $monitorExe = Join-Path $PSScriptRoot '3DMonitor.exe'
    Start-Process -FilePath $monitorExe -ArgumentList '--no-browser' -WindowStyle Hidden
    $health = $null
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try { $health = Invoke-RestMethod -Uri ($baseUrl + '/health') -TimeoutSec 2; break } catch { Start-Sleep -Milliseconds 500 }
    }
    if ($health.app -ne 'K1Monitor') { throw 'Start 3DMonitor first. Check that port 8765 is used by 3DMonitor.' }
    # Check the required API, rather than tying setup to one application version.
    $null = Invoke-RestMethod -Uri ($baseUrl + '/api/v1/remote/status') -TimeoutSec 5
    $page = Invoke-WebRequest -Uri ($baseUrl + '/') -UseBasicParsing
    $match = [regex]::Match($page.Content, 'name="k1-token" content="([^"]+)"')
    if (-not $match.Success) { throw 'Cannot get the local pairing token.' }
    $null = Invoke-RestMethod -Method Post -Uri ($baseUrl + '/api/v1/remote/config') -ContentType 'application/json' -Headers @{'X-K1-Token'=$match.Groups[1].Value} -Body (@{host=$remoteHost} | ConvertTo-Json)
    & $tailscaleExe serve --bg --https=443 --yes http://127.0.0.1:8765
    if ($LASTEXITCODE -ne 0) { throw 'Tailscale Serve was not enabled. Follow the HTTPS setup link if Tailscale shows one.' }
    Write-Host ('Remote URL: https://' + $remoteHost + '/')
    Write-Host 'Open 3DMonitor on this PC and use the Remote connection button to create a pairing code.'
    Write-Host 'On your remote device: turn on Tailscale, open the URL, then enter the code.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
