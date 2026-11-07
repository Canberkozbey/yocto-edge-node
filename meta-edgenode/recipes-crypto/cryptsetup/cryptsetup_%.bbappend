# The build host side only needs veritysetup, which hashes the root
# filesystem image (edgenode-verity.bbclass), and none of cryptsetup's
# optional dependencies; the bundled Argon2 stands in for libargon2, which
# has no recipe. The target is not affected: no image installs cryptsetup.
PACKAGECONFIG:class-native = "veritysetup openssl internal-argon2"
