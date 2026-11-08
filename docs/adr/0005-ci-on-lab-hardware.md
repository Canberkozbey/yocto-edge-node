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

The repository is public. Anyone can open a pull request, and a pull
request runs the workflows of its own branch, new ones included, so a
workflow file cannot keep it off a runner. GitHub recommends self-hosted
runners only for private repositories for this reason.

## Decision

- A second job, `raspberrypi5`, runs after the QEMU job: it builds the Pi
  images, checks them, then serves each over the network and runs the
  development and production tests on the board.
- CI on the self-hosted runner runs for pushes to `main` and manual runs,
  which only collaborators can start, and never for pull requests. A
  concurrency group gives one run at a time the Pi.
- Pull requests, from forks too, get `.github/workflows/checks.yml` on a
  GitHub hosted runner: linters for the workflows, shell scripts and Python
  code, which need no build.
- **The runner enforces this itself.** Its pre-job hook,
  `lab/runner/job-started.sh`, installed root-owned outside the repository
  and the runner's directory, fails every job but pushes to `main`, `v*`
  release tags and manual runs, before any of the job's steps. A pull
  request's own workflow cannot get past it.
- The CI's own steps reach root only through a fixed tool: a root-owned
  copy of `tools/netboot.py` installed as `/usr/local/sbin/edgenode-netboot`.
  A sudo rule (`lab/sudoers.d/edgenode-netboot`) lets the `edgenode-lab`
  group, which the runner's account belongs to, run that command and
  nothing else.
  The installed copy reads only the root-owned `/etc/edgenode/lab.toml` and
  refuses `--config` through sudo, so the caller cannot choose which
  directories root replaces.
- **The served directories are on a `nosuid,nodev` mount, and the tool
  refuses any other.** It extracts the caller's build output as root, with
  owners, modes and extended attributes. Without those mount options, a
  set-user-ID file, a file capability or a device node in that output would
  turn the sudo rule into root on the host for anyone in `edgenode-lab`.
  With them, those take effect only on the Pi, which mounts the files over
  NFS.
- A change to `tools/netboot.py` reaches CI only when an administrator
  reinstalls it. The job warns when the two copies differ.
- The test dependencies, which the runner installs on every run, are
  pinned with the hashes of their files (`tests/requirements.txt`).

## Alternatives considered

- **Approval of each fork's workflow runs as the only guard:** one wrong
  click runs a stranger's code on the build host. The repository requires
  that approval as well (docs/lab.md), but the hook does not depend on it.
- **A private companion repository for the hardware CI:** the strongest
  separation, at the cost of keeping two repositories in step.
- **Passwordless sudo for the repository's own script:** every commit could
  then change what runs as root.
- **A daemon that serves images on request:** a smaller surface still, but
  more to build and run than this lab needs now.
- **Running the job as root:** gives every build step root.

## Consequences

- The tool still extracts build output as root, and parses the image's
  partition table and boot partition (sfdisk, mtools) as root. The trust
  boundary is the repository's collaborators: only their pushes and manual
  runs reach the runner.
- The sudo rule limits what the CI's steps do as root, not what a hostile
  job could: the runner's account is in the `docker` group, which
  `kas-container` needs and which is as good as root. Keeping hostile jobs
  off the runner is the hook's work.
- The lab is shared with local work: a CI run and a local test run at the
  same time disturb each other.
- Pull requests get no build and no hardware tests before merging. A
  maintainer who wants them reviews the changes, pushes them to a branch of
  this repository and runs CI on it by hand.
- The protection lives partly outside the repository: the hook, the mount
  and the repository's settings are set up by hand on the host and on
  GitHub (docs/lab.md).
