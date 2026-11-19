"""The released-update workflow tries again when GitHub answers an app's update check with its rate limit.

The old app's updater asks GitHub's API without a login; GitHub sometimes answers its shared runners
"HTTP Error 403: rate limit exceeded", and the first Mac run of the workflow failed on that alone. The
retry helpers (packaging/github_retry.sh and .ps1) are run here with a stand-in for the app that answers
as it does. The workflow itself is never run by the tests.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "released-update.yml"
RATE_LIMIT = "Could not reach GitHub: HTTP Error 403: rate limit exceeded"     # what the app says (update.check)

# A stand-in for the app: fails with the given answers in turn, then succeeds. Counts its runs in a file.
FAKE = r'''
import sys
from pathlib import Path
count, answers = Path(sys.argv[1]), sys.argv[2:]
n = int(count.read_text()) if count.exists() else 0
count.write_text(str(n + 1))
if n < len(answers):
    sys.exit(answers[n])            # as the app does (cli.py): the message on stderr, exit code 1
print("wormscore 1.26.17 is available")
'''


def git_bash():
    """bash from Git on Windows (System32's bash is WSL), bash elsewhere."""
    if os.name == "nt":
        git = shutil.which("git")
        for cand in ([Path(git).resolve().parents[1] / "bin" / "bash.exe"] if git else []) + \
                    [Path(r"C:\Program Files\Git\bin\bash.exe")]:
            if cand.is_file():
                return str(cand)
        return None
    return shutil.which("bash")


def fake_app(tmp_path):
    script = tmp_path / "fake_app.py"
    script.write_text(FAKE, encoding="utf-8")
    return script


def run_bash(tmp_path, answers, attempts=5):
    bash = git_bash()
    if not bash:
        pytest.skip("bash is not installed")
    count = tmp_path / "count.txt"
    count.unlink(missing_ok=True)
    py = Path(sys.executable).as_posix()
    quoted = " ".join("'" + a.replace("'", "'\\''") + "'" for a in answers)
    cmd = (f"source '{(ROOT / 'packaging' / 'github_retry.sh').as_posix()}' && "
           f"with_github_retry '{py}' '{fake_app(tmp_path).as_posix()}' '{count.as_posix()}' {quoted}")
    env = {**os.environ, "GITHUB_RETRY_WAIT": "0", "GITHUB_RETRY_ATTEMPTS": str(attempts)}
    res = subprocess.run([bash, "-c", cmd], capture_output=True, text=True, env=env, timeout=120)
    return res.returncode, res.stdout + res.stderr, int(count.read_text())


def run_powershell(tmp_path, answers, attempts=5):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is not installed")
    count = tmp_path / "count.txt"
    count.unlink(missing_ok=True)
    quoted = " ".join("'" + a.replace("'", "''") + "'" for a in answers)
    cmd = (f"$ErrorActionPreference = 'Stop'; . '{ROOT / 'packaging' / 'github_retry.ps1'}'; "     # as GitHub runs a step
           f"Invoke-GitHubRetry '{sys.executable}' '{fake_app(tmp_path)}' '{count}' {quoted}; exit $LASTEXITCODE")
    env = {**os.environ, "GITHUB_RETRY_WAIT": "0", "GITHUB_RETRY_ATTEMPTS": str(attempts)}
    res = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True,
                         env=env, timeout=120)
    return res.returncode, res.stdout + res.stderr, int(count.read_text())


@pytest.mark.parametrize("run", [run_bash, run_powershell], ids=["bash", "powershell"])
def test_the_rate_limit_answer_is_tried_again_and_nothing_else_is(tmp_path, run):
    rc, out, n = run(tmp_path, [RATE_LIMIT, "Could not reach GitHub: HTTP Error 429: Too Many Requests"])
    assert (rc, n) == (0, 3) and "is available" in out and out.count("trying again") == 2
    # another failure: once, with its own message and exit code
    rc, out, n = run(tmp_path, ["Could not reach GitHub: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]>"])
    assert (rc, n) == (1, 1) and "CERTIFICATE_VERIFY_FAILED" in out and "trying again" not in out
    rc, out, n = run(tmp_path, ["Could not reach GitHub: HTTP Error 404: Not Found"])
    assert (rc, n) == (1, 1) and "404" in out
    # a rate limit that does not end: a few attempts, then the real message
    rc, out, n = run(tmp_path, [RATE_LIMIT] * 9, attempts=3)
    assert (rc, n) == (1, 3) and out.rstrip().endswith("gave up after 3 attempts") and RATE_LIMIT in out
    # a success is run once
    rc, out, n = run(tmp_path, [])
    assert (rc, n) == (0, 1)


def test_every_step_that_asks_github_without_a_login_tries_again():
    """Each call of the app's updater in released-update goes through the retry; the workflow only reads."""
    flow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert flow["permissions"] == {"contents": "read"}
    assert set(flow[True]) == {"workflow_dispatch"}                    # "on:" reads as True; never run by itself
    calls = 0
    for job in flow["jobs"].values():
        for step in job["steps"]:
            script = step.get("run", "")
            for line in script.splitlines():
                if ("$APP" in line or "$env:APP" in line) and " update" in line and "--rollback" not in line \
                        and "--help" not in line:
                    calls += 1
                    assert "with_github_retry" in line or "Invoke-GitHubRetry" in line, line
            if "with_github_retry" in script:
                assert "packaging/github_retry.sh" in script
            if "Invoke-GitHubRetry" in script:
                assert "packaging\\github_retry.ps1" in script or "packaging/github_retry.ps1" in script
            assert "gh release create" not in script and "gh release upload" not in script and "git push" not in script
    assert calls == 5           # Mac: check, install, check with the new version; Windows: check, install
