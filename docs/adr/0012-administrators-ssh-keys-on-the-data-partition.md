# ADR 0012: Administrators' SSH keys on the data partition

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The production image locks root's password, and sshd lets root in by key
only, OpenSSH's default. The image holds no key, so a production node had
no remote login at all. Reading its logs, installing an update or renewing
its MQTT certificate (ADR 0011) meant taking out its card.

The key cannot go into the image:

- Every node runs the same image, and releases publish it (ADR 0010). A key
  in it would open every node, and changing it would take a release.
- The root filesystem is read-only and verified (ADR 0009), so root's
  `~/.ssh/authorized_keys` cannot be written on the node either.

The data partition, which updates keep, already holds what belongs to one
node: its SSH host keys and its MQTT identity (ADR 0011).

## Decision

- **sshd also reads root's keys from `/data/ssh/authorized_keys`.**
  edgenode-boot, which mounts the data partition, installs
  `/etc/ssh/sshd_config.d/50-edgenode-data-keys.conf`:

  ```
  Match User root
  	AuthorizedKeysFile .ssh/authorized_keys /data/ssh/authorized_keys
  ```

  oe-core's `sshd_config`, and its copy for a read-only root,
  `sshd_config_readonly`, read that directory before their own settings.
  A Match block ends with its file, and its settings win over the global
  ones.
- **For root only.** Without the Match block, the file would serve every
  account with a shell, and only the directory's mode would keep the
  others out: sshd opens the file as the account that logs in.
- **Next to the host keys, root's only.** `edgenode-data.service` makes
  `/data/ssh` 0700 for root at every boot from a slot. sshd's StrictModes
  refuses a key file that another user could change, or one in such a
  directory.
- **The site provisions it,** like the node's identity: on the card's data
  partition before the card goes into the node, or over SSH where a login
  already works (docs/provisioning.md).
- **Nothing else changes.** The production image still has no password
  login and holds no key. A node with no key provisioned has no remote
  login, as before. Network boots have no data partition, so there only the
  home directory counts.

## Testing

- **`tests/image`**, on the Raspberry Pi 5's images: in both images and both
  configurations, root's key files are the home's and
  `/data/ssh/authorized_keys`, in that order, and another account's are not
  on `/data`. The test reads the configuration as sshd does, following
  `Include` and `Match User` (`tests/helpers.py`). The policy test (no empty
  passwords, root by key only in production) reads root's settings too, so
  a Match block for root cannot slip past it.
- **`tests/update`, on the Raspberry Pi 5.** On the card's first boot, a
  key made for the run does not log in. Written to
  `/data/ssh/authorized_keys`, it does. Its line there forces a command,
  and the session must run it: the development image would let root in
  without a password as well, so a login alone would not show that the key
  was taken. After the update to the other slot, the same key still logs
  in.

## Alternatives considered

- **No remote login in production, as before:** nothing to attack over
  the network, but every log, certificate or fix needs the card. A site
  that wants this provisions no key.
- **A key in the image:** the same key on every node, published with every
  release, and changed only by a release.
- **A bind mount of the key directory on `/root/.ssh`,** like the host keys
  (`run-ssh.mount`): one more unit for what one sshd setting does, and it
  would hide the image's own `/root/.ssh`.
- **SSH certificates (`TrustedUserCAKeys`):** the image holds only a CA's
  public key, and administrators get certificates that expire. The better
  tool for a fleet with several administrators. It needs a CA kept offline
  and someone to sign certificates, which one node and one administrator do
  not justify. The drop-in can name the CA next to the file when that
  changes.
- **`AuthorizedKeysCommand`,** asking a service for the keys at each login:
  it needs that service, and the network to reach it, to log in at all.

## Consequences

- **A provisioned production node has a remote root login,** by key only.
  It is as safe as that key.
- **Whoever can write the data partition can add a key.** That takes the
  card or root on the node, and the card already gives everything away
  (SECURITY.md, physical access). Over the network, only root can write
  `/data/ssh`.
- **Taking a key away is per node.** Nothing removes an administrator from
  every node at once; SSH certificates or enrolment would.
