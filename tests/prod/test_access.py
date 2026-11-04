"""Production image: no way in without credentials, and the product still
works from the outside."""

import json

import helpers

SHELL_PROMPT = r"root@[\w-]+:[^ ]+# "


def test_root_console_login_asks_for_password(console):
    console.sendline("root")
    index, _, _, _ = console.expect([r"Password: ", SHELL_PROMPT], timeout=60)
    assert index == 0, "root got a shell without a password"


def test_ssh_rejects_root_without_password(ssh_address):
    assert not helpers.ssh_accepts_empty_password(*ssh_address, user="root")


def test_health_answers_from_host(edgenoded_url):
    status, body = helpers.wait_for_http(f"{edgenoded_url}/health")
    assert status == 200
    assert json.loads(body)["status"] == "ok"
