# Dot-sourced by .github/workflows/released-update.yml (PowerShell steps). The same as github_retry.sh.
#
# An app's updater asks GitHub's API without a login, and GitHub sometimes answers its shared runners
# "HTTP Error 403: rate limit exceeded" (or 429). Invoke-GitHubRetry runs a command, prints what it said,
# and when it failed with that answer tries again after a wait that grows (30 s, 60 s, 120 s, 240 s by
# default). Any other failure is not tried again: its own message and exit code are passed on in
# $LASTEXITCODE.
#
#   Invoke-GitHubRetry $env:APP update --install
#
# The command's output goes through the pipeline, so PowerShell waits for a windowed app to end (as
# "| Out-Host" did). GITHUB_RETRY_ATTEMPTS (default 5) and GITHUB_RETRY_WAIT (first wait in seconds,
# default 30) change the numbers; the tests use them.

function Invoke-GitHubRetry {
  param([Parameter(Mandatory = $true)][string]$Command, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
  $attempts = if ($env:GITHUB_RETRY_ATTEMPTS) { [int]$env:GITHUB_RETRY_ATTEMPTS } else { 5 }
  $wait = if ($env:GITHUB_RETRY_WAIT) { [int]$env:GITHUB_RETRY_WAIT } else { 30 }
  $n = 1
  # GitHub runs PowerShell steps with $ErrorActionPreference = 'Stop'; the app's own message on stderr
  # must come through as text, not stop the step before the answer is read
  $ErrorActionPreference = 'Continue'
  while ($true) {
    $global:LASTEXITCODE = 0
    $out = (& $Command @Arguments 2>&1 | ForEach-Object { "$_" }) -join "`n"
    $rc = $LASTEXITCODE
    if ($out) { Write-Host $out }
    if ($rc -eq 0) { return }
    if ($out -notmatch '(?i)rate limit|HTTP Error (403|429)' -or $n -ge $attempts) {
      if ($n -gt 1) { Write-Host "gave up after $n attempts" }
      $global:LASTEXITCODE = $rc
      return
    }
    Write-Host "GitHub refused the request for now (rate limit); trying again in $wait s (attempt $($n + 1) of $attempts)"
    Start-Sleep -Seconds $wait
    $n += 1
    $wait *= 2
  }
}
