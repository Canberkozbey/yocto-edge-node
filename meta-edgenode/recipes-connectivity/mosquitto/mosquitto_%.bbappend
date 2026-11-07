# edgenoded uses only the client library, libmosquitto1, with TLS for the
# connection to the broker and the node's identity (docs/adr/0011), and
# without websockets (ADR 0003, ADR 0007). The broker and the command-line
# clients are still built, but no image installs them.
PACKAGECONFIG:remove = "websockets"
