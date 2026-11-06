require edgenode-bundle.bb

SUMMARY = "Edge node update bundle, development image"
DESCRIPTION = "The update bundle with the development image's root \
filesystem, which the update tests install (tests/update). Never ship it."

EDGENODE_BUNDLE_IMAGE = "edgenode-image-dev"
