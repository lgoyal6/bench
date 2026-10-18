CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -std=c11
LDLIBS += -lm

solution: solution.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)
