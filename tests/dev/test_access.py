"""Development access. These are also controls for prod/test_access.py:
the same checks must give the opposite result there."""

import helpers


def test_root_logs_in_over_ssh_without_password(ssh_port):
    assert helpers.ssh_accepts_empty_password(ssh_port, user="root")


def test_debug_tools_available(shell):
    shell.run_check("command -v gdbserver")
    shell.run_check("command -v strace")
