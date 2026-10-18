CC ?= cc
harness: harness.c
	$(CC) -O2 -std=c11 -D_POSIX_C_SOURCE=200809L -I../rig -o $@ $<
