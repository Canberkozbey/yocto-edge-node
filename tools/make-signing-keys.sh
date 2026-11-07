#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Creates a PKI that signs and verifies update bundles:
#
#   KEYS_DIR/ca.cert.pem       CA certificate: the keyring in every image
#   KEYS_DIR/signing.cert.pem  signing certificate, issued by the CA
#   KEYS_DIR/signing.key.pem   signing key
#   CA_DIR/ca.key.pem          CA key: issues signing certificates, not
#                              needed by builds
#
#   tools/make-signing-keys.sh dev       development builds: ~/yocto/keys,
#                                        ~/yocto/keys-ca (docs/updates.md)
#   tools/make-signing-keys.sh release   release builds: ~/yocto/release-keys,
#                                        ~/yocto/release-ca (docs/releases.md)
#   tools/make-signing-keys.sh dev|release KEYS_DIR CA_DIR
#
# A build gets KEYS_DIR read-only; the CA key stays out of it. Both
# directories are outside the repository. Development keys are for
# development only: anyone with them can sign updates for images built with
# them. Release keys go into CI's secrets, and the CA key offline.
#
# Images and bundles built with one set of keys trust only that set, so the
# script never replaces existing keys.

set -eu

case "${1:-}" in
dev)
	name="development"
	keys_dir=${2:-"$HOME/yocto/keys"}
	ca_dir=${3:-"$HOME/yocto/keys-ca"}
	;;
release)
	name="release"
	keys_dir=${2:-"$HOME/yocto/release-keys"}
	ca_dir=${3:-"$HOME/yocto/release-ca"}
	;;
*)
	echo "usage: $0 dev|release [KEYS_DIR CA_DIR]" >&2
	exit 2
	;;
esac

for file in "$keys_dir/ca.cert.pem" "$keys_dir/signing.cert.pem" \
	    "$keys_dir/signing.key.pem" "$ca_dir/ca.key.pem"; do
	if [ -e "$file" ]; then
		echo "$file exists; not replacing any keys" >&2
		exit 1
	fi
done

umask 077
mkdir -p "$keys_dir" "$ca_dir"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cat > "$work/ca.ext" <<'EOF'
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
EOF
cat > "$work/signing.ext" <<'EOF'
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = codeSigning
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF

if [ "$name" = development ]; then
	suffix=" (not for production)"
else
	suffix=""
fi

# ECDSA P-256 keys; certificates valid for ten years. The CA certificate
# is self-signed; the signing certificate is issued by it, for code signing
# only (RAUC checks that: check-purpose=codesign in system.conf).
openssl ecparam -name prime256v1 -genkey -noout -out "$work/ca.key.pem"
openssl req -new -sha256 -key "$work/ca.key.pem" \
	-subj "/O=Edge Node/CN=Edge Node $name update CA$suffix" \
	-out "$work/ca.csr"
openssl x509 -req -sha256 -days 3650 -in "$work/ca.csr" -signkey "$work/ca.key.pem" \
	-extfile "$work/ca.ext" -out "$work/ca.cert.pem" 2>/dev/null

openssl ecparam -name prime256v1 -genkey -noout -out "$work/signing.key.pem"
openssl req -new -sha256 -key "$work/signing.key.pem" \
	-subj "/O=Edge Node/CN=Edge Node $name update signing$suffix" \
	-out "$work/signing.csr"
openssl x509 -req -sha256 -days 3650 -in "$work/signing.csr" \
	-CA "$work/ca.cert.pem" -CAkey "$work/ca.key.pem" -CAcreateserial \
	-extfile "$work/signing.ext" -out "$work/signing.cert.pem" 2>/dev/null

openssl verify -CAfile "$work/ca.cert.pem" "$work/signing.cert.pem" >/dev/null
openssl x509 -in "$work/signing.cert.pem" -noout -ext extendedKeyUsage | grep -q "Code Signing"

install -m 0600 "$work/ca.key.pem" "$ca_dir/ca.key.pem"
install -m 0600 "$work/signing.key.pem" "$keys_dir/signing.key.pem"
install -m 0644 "$work/ca.cert.pem" "$keys_dir/ca.cert.pem"
install -m 0644 "$work/signing.cert.pem" "$keys_dir/signing.cert.pem"

echo "Keys for builds: $keys_dir"
echo "CA key:          $ca_dir/ca.key.pem"
