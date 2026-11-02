"""License policy: the production image contains nothing under (L/A)GPLv3.

The image build already enforces this (INCOMPATIBLE_LICENSE in
edgenode-image.bb). This test checks the result independently, from the
license manifest the build writes for each image.
"""

import re

import pytest

import helpers

GPLV3 = {
    "GPL-3.0-only", "GPL-3.0-or-later",
    "LGPL-3.0-only", "LGPL-3.0-or-later",
    "AGPL-3.0-only", "AGPL-3.0-or-later",
}


def _license_ids(expression):
    """SPDX identifiers in a license expression such as "A & (B | C)"."""
    return set(re.findall(r"[A-Za-z0-9.+-]+", expression))


def _gplv3_packages(image):
    return {
        package: expression
        for package, expression in image.licenses.items()
        if _license_ids(expression) & GPLV3
    }


@pytest.fixture(scope="module")
def prod(deploy_dir):
    return helpers.Image(deploy_dir, "edgenode-image")


@pytest.fixture(scope="module")
def dev(deploy_dir):
    return helpers.Image(deploy_dir, "edgenode-image-dev")


def test_license_manifests_cover_installed_packages(prod, dev):
    # One license entry per installed package. The names differ between the
    # two manifests: the image manifest lists installed names (libc6, dbus-1),
    # the license manifest the names before Debian-style renaming (glibc,
    # dbus), so compare the counts.
    for image in (prod, dev):
        assert len(image.licenses) == len(image.packages), (
            f"{image.license_manifest.name}: {len(image.licenses)} license entries "
            f"for {len(image.packages)} installed packages"
        )


def test_no_gplv3_in_production_image(prod):
    offenders = _gplv3_packages(prod)
    assert not offenders, f"GPLv3 packages in the production image: {offenders}"


def test_gplv3_detected_in_development_image(dev):
    # Control: gdbserver is GPLv3, so the check above can see such packages.
    assert "gdbserver" in _gplv3_packages(dev)
