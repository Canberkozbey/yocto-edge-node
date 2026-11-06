# ADR 0005: CI on the lab hardware

- **Status:** Accepted
- **Date:** 2026-11-05

## Context

The self-hosted runner's host is also the lab's build host: it serves the
Raspberry Pi 5's network boot, holds its serial console and reaches the
smart plug that powers it. A CI job can therefore boot every build on the
real board and run the hardware tests, with no one at the bench.

Serving an image needs root: the root filesystem is extracted with its
owners and permissions into the NFS export, and the TFTP directory is
replaced. Whatever CI is allowed to run as root, any commit that reaches CI
can use.

## Decision

- A second job, `raspberrypi5`, runs after the QEMU job: it builds the Pi
  images, checks them, then serves each over the network and runs the
  development and production tests on the board.
- It runs for pushes and manual runs only, never for pull requests, and a
  concurrency group gives one run at a time the Pi.
- Root is reached only through a fixed tool: a root-owned copy of
  `tools/netboot.py` installed as `/usr/local/sbin/edgenode-netboot`. A sudo
  rule (`lab/sudoers.d/edgenode-netboot`) lets the `edgenode-lab` group,
  which the runner's account belongs to, run that command and nothing else.
  The installed copy reads only the root-owned `/etc/edgenode/lab.toml` and
  refuses `--config` through sudo, so the caller cannot choose which
  directories root replaces.
- A change to `tools/netboot.py` reaches CI only when an administrator
  reinstalls it. The job warns when the two copies differ.

## Alternatives considered

- **Passwordless sudo for the repository's own script:** every commit could
  then change what runs as root.
- **A daemon that serves images on request:** a smaller surface still, but
  more to build and run than this lab needs now.
- **Running the job as root:** gives every build step root.

## Consequences

- The tool still extracts build output as root. The trust boundary is the
  repository: the runner only takes jobs for this private repository, and
  its collaborators are trusted.
- The lab is shared with local work: a CI run and a local test run at the
  same time disturb each other.
- **Before the repository goes public**, self-hosted jobs must not run on
  pull requests from forks: a pull request can change the workflow and run
  any code on the runner. The QEMU job's `pull_request` trigger has to go,
  or move to approval-gated or GitHub-hosted runners, at the same time.
