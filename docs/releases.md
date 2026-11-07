# Releases

A release is a tag `vX.Y.Z` and the GitHub release that the release
workflow makes from it, for the Raspberry Pi 5. This page covers what a
release holds, how to check one, and how to make one. The reasons are in
[ADR 0010](adr/0010-sbom-cve-reports-and-signed-releases.md).

## What a release holds

| File | What it is |
| --- | --- |
| `edgenode-X.Y.Z-raspberrypi5.wic.bz2`, `.wic.bmap` | SD card image: the production image in slot A, slot B empty |
| `edgenode-X.Y.Z-raspberrypi5.raucb` | Update bundle, signed with the release key |
| `edgenode-X.Y.Z-raspberrypi5.spdx.json` | Software bill of materials (SPDX 3.0): every package, its version, license and sources |
| `edgenode-X.Y.Z-raspberrypi5.cve.json` | CVE report at build time, per package, every CVE with its status |
| `edgenode-X.Y.Z-raspberrypi5.cve.txt` | The same, only the unpatched ones, one line per recipe |
| `edgenode-release-ca.cert.pem` | The CA certificate that the image trusts for updates |
| `SHA256SUMS` | SHA-256 checksums of the files above |

The release notes list the release CA's fingerprint and summarise the CVE
report. The image trusts only the release CA, so it installs only release
bundles. A released node runs the production image: no password login, and
SSH by key only.

## Checking a release

Download the files into one directory, then:

```sh
sha256sum -c SHA256SUMS
```

Every file has a build provenance attestation, a statement signed through
Sigstore that this repository's release workflow built it from the tag. The
GitHub CLI checks it (`gh`, version 2.49 or later). The release notes give
the command with the repository's name filled in; for the SD card image of
release 0.1.0 it reads:

```sh
REPO=owner/yocto-edge-node   # the repository, as in its GitHub URL
gh attestation verify edgenode-0.1.0-raspberrypi5.wic.bz2 --repo "$REPO" \
    --signer-workflow "$REPO/.github/workflows/release.yml"
```

The bundle's signature can be checked as a node checks it before
installing, with `rauc` (Ubuntu package `rauc`) and a minimal configuration
that trusts the release CA:

```sh
cat > rauc-verify.conf <<'EOF'
[system]
compatible=edgenode-raspberrypi5
bootloader=noop

[keyring]
path=edgenode-release-ca.cert.pem
check-purpose=codesign
EOF
rauc info --conf=rauc-verify.conf edgenode-0.1.0-raspberrypi5.raucb
```

It prints `Verified inline signature by 'O = Edge Node, CN = Edge Node
release update signing'`, then the bundle's contents.

Writing the card and installing a bundle work as for development builds
([updates.md](updates.md)), with the release's files.

## Making a release

### Once: release keys and the release environment

Create the release keys on a machine you trust, preferably not the build
host:

```sh
tools/make-signing-keys.sh release
```

| File | Goes to |
| --- | --- |
| `~/yocto/release-keys/ca.cert.pem` | secret `RELEASE_CA_CERT` |
| `~/yocto/release-keys/signing.cert.pem` | secret `RELEASE_SIGNING_CERT` |
| `~/yocto/release-keys/signing.key.pem` | secret `RELEASE_SIGNING_KEY` |
| `~/yocto/release-ca/ca.key.pem` | offline storage only, never GitHub |

On GitHub, in the repository's **Settings → Environments**:

1. **New environment**, named `release`.
2. Under **Deployment branches and tags**, choose **Selected branches and
   tags** and add a rule of type tag with the pattern `v*`. Then only jobs
   for `v*` tags get the secrets.
3. Under **Environment secrets**, add the three secrets in the table, each
   with the whole content of its file, `-----BEGIN` and `-----END` lines
   included.

With the GitHub CLI, step 3 is:

```sh
gh secret set RELEASE_CA_CERT --env release < ~/yocto/release-keys/ca.cert.pem
gh secret set RELEASE_SIGNING_CERT --env release < ~/yocto/release-keys/signing.cert.pem
gh secret set RELEASE_SIGNING_KEY --env release < ~/yocto/release-keys/signing.key.pem
```

Then copy `~/yocto/release-keys` and `~/yocto/release-ca` to offline
storage, such as an encrypted USB stick, and delete them from the machine.
Without a backup, a lost key means new SD cards for every released node
(ADR 0010).

Recommended: in **Settings → Rules → Rulesets**, add a tag ruleset for
`v*` that restricts updates and deletions, so a published release's tag
cannot be moved.

The release builds on the same self-hosted runner as CI, in its own build
directory (`~/yocto/release-work`), with the shared download and shared
state caches. The runner host needs no other setup.

### Each release

1. Set `DISTRO_VERSION` in `meta-edgenode/conf/distro/edgenode.conf` to
   the new version, commit and push.
2. Wait until CI has passed on that commit. The release checks this and
   does not run the hardware tests again.
3. Tag the commit and push the tag:

   ```sh
   git tag -a v0.1.0 -m "Edge Node 0.1.0"
   git push origin v0.1.0
   ```

The release workflow (`.github/workflows/release.yml`) then:

- checks that the tag matches `DISTRO_VERSION`, that the repository is
  public (attestations need that), and that CI passed on the commit;
- writes the release keys to `/dev/shm` on the runner, checks that they
  belong together and are not the development keys, and builds
  `kas/release.yml`;
- deletes the keys as soon as the build ends, whether it succeeded or not;
- runs the image tests, which also check that both images trust the
  release CA and that no development bundle was built;
- collects the files, writes `SHA256SUMS`, attests every file and
  publishes the release.

If a step fails, nothing is published. Fix the cause, delete the tag
(`git push origin :refs/tags/v0.1.0` and `git tag -d v0.1.0`), and tag
again.
