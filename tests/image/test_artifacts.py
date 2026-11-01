"""Checks on the built images themselves, without booting them: installed
packages from the manifests, files read straight from the ext4 images."""

import re

import pytest

import helpers

DEBUG_TOOLS = {"gdbserver", "strace"}
# Package names of the Mesa/OpenGL, Wayland and X11 stacks. Precise on
# purpose: a plain "libgl" prefix also matches GLib (libglib-2.0-0).
GRAPHICS_PACKAGE = re.compile(
    r"^(mesa|libgl-|libgl1|libglapi|libglvnd|libglx|libegl|libgles|libgbm|libwayland|wayland|libx11|xserver)"
)


@pytest.fixture(scope="module")
def prod(deploy_dir):
    return helpers.Image(deploy_dir, "edgenode-image")


@pytest.fixture(scope="module")
def dev(deploy_dir):
    return helpers.Image(deploy_dir, "edgenode-image-dev")


def test_debug_tools_only_in_dev(prod, dev):
    assert DEBUG_TOOLS <= dev.packages
    assert not DEBUG_TOOLS & prod.packages


def test_root_account_locked_in_prod(prod, dev):
    prod_password = prod.shadow_entry("root")[1]
    assert prod_password.startswith(("*", "!")), f"root password field: {prod_password!r}"
    assert dev.shadow_entry("root")[1] == ""  # control


def test_sshd_policy(prod, dev):
    prod_cfg = prod.sshd_settings()
    # Unset means the OpenSSH default: no empty passwords, root by key only.
    assert prod_cfg.get("permitemptypasswords", "no") == "no"
    assert prod_cfg.get("permitrootlogin", "prohibit-password") != "yes"

    dev_cfg = dev.sshd_settings()  # control
    assert dev_cfg.get("permitemptypasswords") == "yes"
    assert dev_cfg.get("permitrootlogin") == "yes"


def test_no_graphics_stack(prod, dev):
    for image in (prod, dev):
        offenders = sorted(p for p in image.packages if GRAPHICS_PACKAGE.match(p))
        assert not offenders, f"{image.manifest.name}: {offenders}"


def test_both_images_run_edgenoded(prod, dev):
    for image in (prod, dev):
        assert {"edgenoded", "edgenode-config"} <= image.packages
