# Rig-side tools, built on the QNX Pi itself (QNX Developer Desktop toolchain).
# They also build on Linux so the unit tests can run them.

CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -std=c11
CPPFLAGS += -D_POSIX_C_SOURCE=200809L
ifeq ($(shell uname -s),Linux)
CPPFLAGS += -D_GNU_SOURCE
LDLIBS += -lpthread
endif

BINS = injector cpuhog rtlatency schedprobe

all: $(BINS)

%: %.c bench_trace.h bench_pin.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -f $(BINS)

.PHONY: all clean
