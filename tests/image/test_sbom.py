"""The images' software bill of materials and CVE report (docs/adr/0010).

Every build writes an SBOM per image: SPDX 3.0, from oe-core's create-spdx.
Builds with kas/cve-check.yml (CI's hardware job, releases) also check it
against the CVE databases with sbom-cve-check; EDGENODE_CVE_CHECK=1 tells
these tests to expect the report. Report only: known CVEs do not fail
them, a missing or empty report does.
"""

import functools
import json

import pytest

import helpers

IMAGES = ("edgenode-image", "edgenode-image-dev")
needs_cve_check = pytest.mark.skipif(
    not helpers.CVE_CHECK, reason="the build ran no CVE check (EDGENODE_CVE_CHECK)")


def deploy_file(deploy_dir, image, suffix):
    return deploy_dir / f"{image}-{helpers.MACHINE}.rootfs.{suffix}"


@functools.cache
def sbom_elements(path):
    """The SBOM's elements, by type: {type: [element]}."""
    document = json.loads(path.read_text())
    assert "spdx.org/rdf/3.0" in json.dumps(document["@context"])
    elements = {}
    for element in document["@graph"]:
        elements.setdefault(element.get("type"), []).append(element)
    return elements


@pytest.mark.parametrize("image", IMAGES)
def test_sbom_covers_every_installed_package(deploy_dir, image):
    elements = sbom_elements(deploy_file(deploy_dir, image, "spdx.json"))
    packages = elements["software_Package"]

    def purpose(package):
        # Compact ("install") or the full IRI, depending on the context.
        return package.get("software_primaryPurpose", "").rpartition("/")[2]

    # The image's root file system, and a package element for each package
    # it installs, by the name in the image manifest.
    assert any(p.get("name") == image and purpose(p) == "archive" for p in packages)
    installed = {p["name"] for p in packages if purpose(p) == "install"}
    missing = helpers.Image(deploy_dir, image).packages - installed
    assert not missing, f"installed but not in the SBOM: {sorted(missing)}"
    assert "edgenoded" in installed
    # Every package carries its license.
    assert elements["simplelicensing_LicenseExpression"]


@pytest.mark.parametrize("image", IMAGES)
def test_image_files_named_after_the_version(deploy_dir, image):
    # The distro version where oe-core puts the build time (edgenode.conf).
    # New CVE data runs the CVE check again on its own, and it looks for the
    # SBOM under the image's name in that build: an image built earlier, or
    # taken from the shared state cache, must have the same name.
    lines = helpers.Image(deploy_dir, image).read_file("/usr/lib/os-release").splitlines()
    version = dict(line.split("=", 1) for line in lines if "=" in line)["VERSION_ID"].strip('"')
    sbom = deploy_file(deploy_dir, image, "spdx.json").resolve()
    assert sbom.name == f"{image}-{helpers.MACHINE}.rootfs-{version}.spdx.json"


@needs_cve_check
@pytest.mark.parametrize("image", IMAGES)
def test_cve_report_written(deploy_dir, image):
    report = json.loads(deploy_file(deploy_dir, image, "sbom-cve-check.yocto.json").read_text())
    builds = {package["name"]: package for package in report["package"]}
    # The report covers the image's recipes, the kernel and our own
    # application among them; the kernel always has CVE records.
    kernel = "linux-raspberrypi" if helpers.MACHINE == "raspberrypi5" else "linux-yocto"
    assert {kernel, "edgenoded"} <= set(builds)
    assert builds[kernel]["issue"]
    statuses = {issue["status"] for package in report["package"] for issue in package["issue"]}
    assert statuses <= {"Patched", "Unpatched", "Ignored"}
    # The readable summary: one line per recipe with unpatched CVEs, so
    # empty when there are none.
    assert deploy_file(deploy_dir, image, "cve.txt").is_file()


@needs_cve_check
@pytest.mark.skipif(helpers.MACHINE != "raspberrypi5", reason="the Raspberry Pi kernel's source list")
@pytest.mark.parametrize("image", IMAGES)
def test_cve_report_leaves_out_kernel_code_not_built(deploy_dir, image):
    # The kernel's debug information gives the source files it builds
    # (debug-info.cfg), and a CVE in files it does not build is not
    # affected. Without that list every file of the tree counts as built,
    # and the report lists every kernel CVE.
    report = json.loads(deploy_file(deploy_dir, image, "sbom-cve-check.yocto.json").read_text())
    kernel = next(p for p in report["package"] if p["name"] == "linux-raspberrypi")
    not_built = [i for i in kernel["issue"] if i.get("detail") == "not-applicable-config"]
    assert not_built and all(i["status"] == "Ignored" for i in not_built)
