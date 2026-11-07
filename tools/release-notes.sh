#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Writes a release's notes (Markdown) to stdout: what each file is, how to
# check it, and the CVE summary. The release workflow
# (.github/workflows/release.yml) calls it once the files are collected:
#
#   tools/release-notes.sh VERSION RELEASE_DIR CVE_REPORT
#
# GITHUB_SERVER_URL and GITHUB_REPOSITORY, set in GitHub Actions, give the
# repository the commands point at.

set -eu

if [ $# -ne 3 ]; then
	echo "usage: $0 VERSION RELEASE_DIR CVE_REPORT" >&2
	exit 2
fi
version=$1
dir=$2
report=$3
repo=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must name the repository}
server=${GITHUB_SERVER_URL:-https://github.com}
name="edgenode-$version-raspberrypi5"

for file in "$name.wic.bz2" "$name.wic.bmap" "$name.raucb" "$name.spdx.json" \
	    "$name.cve.json" "$name.cve.txt" edgenode-release-ca.cert.pem SHA256SUMS; do
	if [ ! -f "$dir/$file" ]; then
		echo "$dir/$file missing" >&2
		exit 1
	fi
done

fingerprint=$(openssl x509 -in "$dir/edgenode-release-ca.cert.pem" -noout -fingerprint -sha256 | cut -d= -f2)
script_dir=$(dirname "$0")

cat <<EOF
Edge Node $version for the Raspberry Pi 5: the production image, its update
bundle, and what it is made of. Built and signed by this repository's
release workflow; see [docs/releases.md]($server/$repo/blob/v$version/docs/releases.md).

| File | What it is |
| --- | --- |
| \`$name.wic.bz2\`, \`$name.wic.bmap\` | SD card image, the production image in slot A; write it with \`bmaptool\` |
| \`$name.raucb\` | Update bundle, for nodes that run a release |
| \`$name.spdx.json\` | Software bill of materials (SPDX 3.0) |
| \`$name.cve.json\`, \`$name.cve.txt\` | CVE report at build time (sbom-cve-check) |
| \`edgenode-release-ca.cert.pem\` | The CA certificate the image trusts for updates |
| \`SHA256SUMS\` | SHA-256 checksums of the files above |

Release CA, SHA-256 fingerprint: \`$fingerprint\`

## Checking the files

Every file has a build provenance attestation: a signed statement that this
repository's release workflow built it from tag v$version.

\`\`\`sh
sha256sum -c SHA256SUMS
gh attestation verify $name.wic.bz2 --repo $repo \\
    --signer-workflow $repo/.github/workflows/release.yml
\`\`\`

The same for each file. docs/releases.md also shows how to check the
bundle's signature with \`rauc\`, as a node does before it installs it.

EOF
"$script_dir/cve-summary.py" "$report" "Known CVEs at build time"
