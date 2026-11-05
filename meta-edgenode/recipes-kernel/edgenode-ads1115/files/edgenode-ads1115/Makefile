# SPDX-License-Identifier: GPL-2.0-only
# Out-of-tree build against a kernel tree, as module.bbclass calls it:
#   make KERNEL_SRC=<kernel build directory>
obj-m := edgenode-ads1115.o

KERNEL_SRC ?= /lib/modules/$(shell uname -r)/build
SRC := $(CURDIR)

all:
	$(MAKE) -C $(KERNEL_SRC) M=$(SRC) modules

modules_install:
	$(MAKE) -C $(KERNEL_SRC) M=$(SRC) modules_install

clean:
	$(MAKE) -C $(KERNEL_SRC) M=$(SRC) clean
