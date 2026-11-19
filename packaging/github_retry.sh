# Sourced by .github/workflows/released-update.yml (bash steps).
#
# An app's updater asks GitHub's API without a login, and GitHub sometimes answers its shared runners
# "HTTP Error 403: rate limit exceeded" (or 429). with_github_retry runs a command, prints what it said,
# and when it failed with that answer tries again after a wait that grows (30 s, 60 s, 120 s, 240 s by
# default). Any other failure is not tried again: its own message and exit code are passed on.
#
#   with_github_retry "$APP" update --install
#
# GITHUB_RETRY_ATTEMPTS (default 5) and GITHUB_RETRY_WAIT (first wait in seconds, default 30) change the
# numbers; the tests use them.

with_github_retry() {
  local attempts="${GITHUB_RETRY_ATTEMPTS:-5}" wait="${GITHUB_RETRY_WAIT:-30}" n=1 rc log
  log="$(mktemp)"
  while :; do
    # to a file, not through a pipe: an updater that leaves a helper running would hold a pipe open
    rc=0
    "$@" >"$log" 2>&1 || rc=$?
    cat "$log"
    if [ "$rc" -eq 0 ]; then rm -f "$log"; return 0; fi
    if ! grep -Eqi 'rate limit|HTTP Error (403|429)' "$log" || [ "$n" -ge "$attempts" ]; then
      [ "$n" -gt 1 ] && echo "gave up after $n attempts" >&2
      rm -f "$log"
      return "$rc"
    fi
    echo "GitHub refused the request for now (rate limit); trying again in ${wait} s (attempt $((n + 1)) of $attempts)" >&2
    sleep "$wait"
    n=$((n + 1))
    wait=$((wait * 2))
  done
}
