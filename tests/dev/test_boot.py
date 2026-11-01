"""The image boots into the expected, healthy system."""


def test_boot_completes_without_failed_units(shell):
    state = shell.run("systemctl is-system-running")[0]
    failed = shell.run("systemctl --failed --no-legend --plain")[0]
    assert state == ["running"], f"system is {state}, failed units: {failed}"


def test_runs_edgenode_distro(shell):
    os_release = dict(
        line.split("=", 1) for line in shell.run_check("cat /etc/os-release") if "=" in line
    )
    assert os_release["ID"] == "edgenode"


def test_init_is_systemd(shell):
    assert shell.run_check("cat /proc/1/comm") == ["systemd"]
