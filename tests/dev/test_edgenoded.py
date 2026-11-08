"""edgenoded runs as intended and serves its health endpoint."""

import json
import socket
import threading
import time
import urllib.parse

import pytest

import helpers


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
    status, body = helpers.http_get(f"{edgenoded_url}/health")
    assert status == 200
    assert json.loads(body)["version"] == installed


def test_unknown_path_is_404(edgenoded_url):
    status, _ = helpers.http_get(f"{edgenoded_url}/no-such-endpoint")
    assert status == 404


def test_slow_client_cannot_hold_the_service(edgenoded_url):
    # A client gets 2 s in all to send its request, however it spreads the
    # bytes. Without that limit, one client sending a byte now and then held
    # the service's loop: no answers, and past the watchdog a restart, which
    # also fails a trial boot (edgenode-boot-commit).
    url = urllib.parse.urlsplit(edgenoded_url)
    slow = socket.create_connection((url.hostname, url.port), timeout=10)
    cut_off = threading.Event()

    def trickle():
        stop = time.monotonic() + 10
        try:
            while time.monotonic() < stop:
                slow.send(b"G")
                time.sleep(0.5)
        except OSError:
            cut_off.set()

    thread = threading.Thread(target=trickle)
    thread.start()
    try:
        time.sleep(0.5)
        start = time.monotonic()
        try:
            status, _ = helpers.http_get(f"{edgenoded_url}/health", timeout=8)
        except TimeoutError:
            pytest.fail("/health got no answer behind a slow client")
        waited = time.monotonic() - start
        assert status == 200
        assert waited < 4, f"/health took {waited:.1f} s behind a slow client"
        assert cut_off.wait(timeout=6), "the slow client was not cut off"
    finally:
        slow.close()
        thread.join()
