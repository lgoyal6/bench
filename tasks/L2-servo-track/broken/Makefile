# The rig stages the task harness next to the solution, so l2_api.h is in ../harness.
CC ?= cc
CPPFLAGS += -D_POSIX_C_SOURCE=200809L -I../harness
CFLAGS ?= -O2 -Wall -Wextra -std=c11
ifeq ($(shell uname -s),Linux)
LDLIBS += -lpthread -lrt
endif
LDLIBS += -lm

solution: controller.c
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ $< $(LDLIBS)
