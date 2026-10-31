# Raspberry Pi specific metadata

Recipes and bbappends that only make sense on Raspberry Pi hardware go here,
under the usual `recipes-*/<name>/` layout.

`BBFILES_DYNAMIC` in `conf/layer.conf` parses this directory only when
meta-raspberrypi (collection `raspberrypi`) is part of the build. Builds for
other machines never see these files, so `meta-edgenode` has no hard
dependency on any BSP layer.
