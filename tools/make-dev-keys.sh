#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Creates the development PKI that signs and verifies update bundles
# (docs/updates.md):
#
#   KEYS_DIR/ca.cert.pem   CA certificate: the keyring in every image
#   KEYS_DIR/dev.cert.pem  signing certificate, issued by the CA
#   KEYS_DIR/dev.key.pem   signing key
#   CA_DIR/ca.key.pem      CA key: issues signing certificates, not needed
#                          by builds
#
# The build container gets KEYS_DIR read-only; the CA key stays out of it.
# Both directories are outside the repository, and the keys are for
# development only: anyone with them can sign updates for these images.
#
# Images and bundles built with one set of keys trust only that set, so the
# script never replaces existing keys.
#
#   tools/make-dev-keys.sh                  # ~/yocto/keys and ~/yocto/keys-ca
#   tools/make-dev-keys.sh KEYS_DIR CA_DIR

set -eu

keys_dir=${1:-"$HOME/yocto/keys"}
ca_dir=${2:-"$HOME/yocto/keys-ca"}

for file in "$keys_dir/ca.cert.pem" "$keys_dir/dev.cert.pem" \
	    "$keys_dir/dev.key.pem" "$ca_dir/ca.key.pem"; do
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
cat > "$work/dev.ext" <<'EOF'
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = codeSigning
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF

# ECDSA P-256 keys; certificates valid for ten years. The CA certificate
# is self-signed; the signing certificate is issued by it, for code signing
# only (RAUC checks that: check-purpose=codesign in system.conf).
openssl ecparam -name prime256v1 -genkey -noout -out "$work/ca.key.pem"
openssl req -new -sha256 -key "$work/ca.key.pem" \
	-subj "/O=Edge Node/CN=Edge Node development update CA (not for production)" \
	-out "$work/ca.csr"
openssl x509 -req -sha256 -days 3650 -in "$work/ca.csr" -signkey "$work/ca.key.pem" \
	-extfile "$work/ca.ext" -out "$work/ca.cert.pem" 2>/dev/null

openssl ecparam -name prime256v1 -genkey -noout -out "$work/dev.key.pem"
openssl req -new -sha256 -key "$work/dev.key.pem" \
	-subj "/O=Edge Node/CN=Edge Node development update signing (not for production)" \
	-out "$work/dev.csr"
openssl x509 -req -sha256 -days 3650 -in "$work/dev.csr" \
	-CA "$work/ca.cert.pem" -CAkey "$work/ca.key.pem" -CAcreateserial \
	-extfile "$work/dev.ext" -out "$work/dev.cert.pem" 2>/dev/null

openssl verify -CAfile "$work/ca.cert.pem" "$work/dev.cert.pem" >/dev/null
openssl x509 -in "$work/dev.cert.pem" -noout -ext extendedKeyUsage | grep -q "Code Signing"

install -m 0600 "$work/ca.key.pem" "$ca_dir/ca.key.pem"
install -m 0600 "$work/dev.key.pem" "$keys_dir/dev.key.pem"
install -m 0644 "$work/ca.cert.pem" "$keys_dir/ca.cert.pem"
install -m 0644 "$work/dev.cert.pem" "$keys_dir/dev.cert.pem"

echo "Keys for builds: $keys_dir"
echo "CA key:          $ca_dir/ca.key.pem"
