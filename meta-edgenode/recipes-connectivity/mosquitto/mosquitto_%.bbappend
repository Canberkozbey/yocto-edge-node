# edgenoded uses only the client library, libmosquitto1, over plain MQTT for
# now: no TLS until stage 4 brings keys and certificates, and no websockets
# (ADR 0003, ADR 0007). The broker and the command-line clients are still
# built, but no image installs them.
PACKAGECONFIG:remove = "ssl websockets"
