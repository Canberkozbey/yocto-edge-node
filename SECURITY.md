# Security

## Reporting a vulnerability

Please report a vulnerability privately, through GitHub's private
vulnerability reporting: the repository's **Security** tab, **Report a
vulnerability**. Please do not open a public issue for it. This is a
one-person project with no bug bounty; reports are answered there.

In scope: this repository's own code and configuration, which are the
`meta-edgenode` layer, `edgenoded`, `edgenode-boot`, the tools, the tests
and the workflows. Issues in upstream projects (the Yocto Project layers,
meta-raspberrypi, meta-rauc, RAUC, Mosquitto) belong with those projects.

Fixes go to `main`; there is no maintained release branch.

## What the node protects against

| What | How | Where |
| --- | --- | --- |
| Changed or corrupted software | The root filesystem is read-only and verified block by block by dm-verity; a slot that fails verification rolls back | [ADR 0009](docs/adr/0009-read-only-root-with-dm-verity.md) |
| Forged or broken updates | Update bundles must be signed by a code-signing certificate from the CA in the image, and their payload is verified as it is read | [ADR 0008](docs/adr/0008-ab-updates-with-rauc-and-tryboot.md), [ADR 0010](docs/adr/0010-sbom-cve-reports-and-signed-releases.md) |
| Development builds on released nodes | Releases are signed with separate keys, kept in a GitHub environment and offline; released images trust only the release CA | [docs/releases.md](docs/releases.md) |
| Logging in | The production image has no password login. SSH takes root by key only, and the image holds no key: only keys provisioned on a node's data partition let root in | [ADR 0012](docs/adr/0012-administrators-ssh-keys-on-the-data-partition.md) |
| A compromised service | `edgenoded` runs as a dynamic user, with no capabilities, a system call filter and a read-only view of the system | its unit, `edgenoded.service` |
| Eavesdropping on or faking the broker or the node | MQTT runs over mutual TLS: each side checks the other's certificate, the broker's against its address; the node's key is readable only by `edgenoded`'s group | [ADR 0011](docs/adr/0011-device-identity-and-mqtt-over-mutual-tls.md) |
| Unknown software and known CVEs | Each image has an SBOM, a CVE report and, for releases, build provenance attestations | [ADR 0010](docs/adr/0010-sbom-cve-reports-and-signed-releases.md) |

## What it does not protect against

- **Physical access.** There is no secure boot: the Raspberry Pi 5's
  signing key would have to be programmed into its OTP memory, which is
  irreversible and deliberately not done on the lab's board. Whoever holds
  the SD card can change the boot partitions, and with them the root hash
  that dm-verity checks against. The data partition is neither encrypted
  nor verified: whoever holds the card can copy the node's MQTT key, or add
  an SSH key that logs in as root.
- **The local network reading the node.** `edgenoded`'s HTTP API (port
  8080) is read-only, unauthenticated and plain HTTP: health and sensor
  readings, for the local network. Anyone who can reach the node can read
  them. It limits how long a client may take, so a slow one cannot stall
  the service.
- **Known CVEs.** The CVE report informs; it does not fail a build.

## The lab and CI

CI and releases build on a self-hosted runner, which is the lab's build
host. It never takes a pull request, whatever a workflow file says: pull
requests get linters on GitHub's own runners, and the runner's job hook
refuses every job but pushes to `main`, release tags and manual runs. The
release keys reach that host only in memory and only during a release
build, and the build job cannot write to the repository or sign
attestations; a separate job on a GitHub hosted runner publishes. The
reasons are in [ADR 0005](docs/adr/0005-ci-on-lab-hardware.md) and
[ADR 0010](docs/adr/0010-sbom-cve-reports-and-signed-releases.md), and the
host and repository settings that go with them in [docs/lab.md](docs/lab.md).

No key is ever in this repository: development keys live on the build host,
release keys in the `release` environment's secrets and offline
([docs/updates.md](docs/updates.md), [docs/releases.md](docs/releases.md)).
The lab's network boot trusts the local network (docs/lab.md).
