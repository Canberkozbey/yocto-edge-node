"""edgenoded runs as intended and serves its health endpoint."""

import json
import urllib.error
import urllib.request


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def test_service_is_active(shell):
    assert shell.run_check("systemctl is-active edgenoded") == ["active"]


def test_runs_unprivileged(shell):
    pid = shell.run_check("systemctl show --property=MainPID --value edgenoded")[0]
    uid_line = shell.run_check(f"grep '^Uid:' /proc/{pid}/status")[0]
    real_uid = uid_line.split()[1]
    assert real_uid != "0", "edgenoded must not run as root"


def test_watchdog_is_armed(shell):
    value = shell.run_check("systemctl show --property=WatchdogUSec --value edgenoded")[0]
    assert value not in ("0", "infinity", ""), f"WatchdogUSec={value}"


def test_config_file_installed(shell):
    lines = shell.run_check("cat /etc/edgenoded.conf")
    assert any(line.replace(" ", "") == "port=8080" for line in lines)


def test_health_on_target(shell):
    body = shell.run_check("wget -qO- http://127.0.0.1:8080/health")
    health = json.loads("".join(body))
    assert health["status"] == "ok"


def test_health_reports_installed_version(shell, edgenoded_url):
    installed = shell.run_check("edgenoded --version")[0].split()[-1]
    status, body = _get(f"{edgenoded_url}/health")
    assert status == 200
    assert json.loads(body)["version"] == installed


def test_unknown_path_is_404(edgenoded_url):
    status, _ = _get(f"{edgenoded_url}/no-such-endpoint")
    assert status == 404
