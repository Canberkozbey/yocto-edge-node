# ADR 0001: Build setup with kas and separate upstream repositories

- **Status:** Accepted
- **Date:** 2026-10-31

## Context

The build needs to be reproducible on a developer machine and in CI, and it
has to cover more than one machine (QEMU now, Raspberry Pi 5 later).

Since Yocto 5.3 "whinlatter" the combined poky repository is no longer
updated. Builds use bitbake, openembedded-core and meta-yocto as separate
repositories, set up either by hand or with `bitbake-setup`.

## Decision

- Use **kas** to describe the build: repositories, revisions, layers,
  distro, machine and local configuration in versioned YAML files.
- Build inside **kas-container**, so the developer machine and CI share the
  same build environment.
- Base on **Yocto 6.0 "wrynose"**, the current LTS release.
- Pin bitbake, openembedded-core and meta-yocto to a **`yocto-6.0.x` release
  tag plus its commit**. kas checks that the tag still points to the commit.
- Pin third-party layers that have no release tags with **`kas lock`**, into
  `kas/base.lock.yml`.
- Split configuration into `kas/base.yml` plus one file per machine,
  combined on the command line (`kas/base.yml:kas/machine/<machine>.yml`).

## Alternatives considered

- **bitbake-setup:** the new upstream tool, with configuration fragments and
  shared state support. kas was chosen because it also covers containerised
  builds, lock files and patching layers, and is widely used in CI.
- **Following release branches:** picks up fixes between point releases, but
  the build changes without a commit in this repository.

## Consequences

- Updating upstream is an explicit commit: change tag and commit together.
- The release tags are GPG-signed. Signature verification in kas
  (`signed: true`) is a planned follow-up, after the signing key for the 6.0
  point releases is confirmed.
