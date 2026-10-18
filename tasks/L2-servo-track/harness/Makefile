CC ?= cc
CPPFLAGS += -D_POSIX_C_SOURCE=200809L -I../rig
CFLAGS ?= -O2 -Wall -Wextra -std=c11
ifeq ($(shell uname -s),Linux)
LDLIBS += -lpthread -lrt
endif
LDLIBS += -lm

harness: harness.c l2_api.h servo_out.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ $< $(LDLIBS)
