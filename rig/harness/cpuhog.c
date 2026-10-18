/*
 * cpuhog: burn a share of one core for a fixed time. Used by the fault injector's cpu_hog.
 *
 *   cpuhog --core N --ms DURATION [--load PCT] [--prio P]
 *
 * Load is applied as a duty cycle over 10 ms windows.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bench_pin.h"
#include "bench_trace.h"

#define WINDOW_NS 10000000ull

int main(int argc, char **argv)
{
    int core = -1, load = 100, prio = 0;
    long ms = -1;
    char msg[64];

    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--core"))
            core = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--ms"))
            ms = atol(argv[i + 1]);
        else if (!strcmp(argv[i], "--load"))
            load = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--prio"))
            prio = atoi(argv[i + 1]);
    }
    if (ms < 0 || load < 1 || load > 100) {
        fprintf(stderr, "usage: cpuhog --core N --ms DURATION [--load 1-100] [--prio P]\n");
        return 2;
    }
    if (bench_pin_core(core) != 0) {
        perror("cpuhog: pin core");
        return 1;
    }
    if (bench_set_fifo(prio) != 0) {
        fprintf(stderr, "cpuhog: could not set priority %d\n", prio);
        return 1;
    }

    snprintf(msg, sizeof msg, "cpuhog core %d load %d%% for %ld ms", core, load, ms);
    bench_mark(BENCH_MARK_FAULT, msg);

    uint64_t end = bench_now_ns() + (uint64_t)ms * 1000000ull;
    uint64_t busy = WINDOW_NS * (uint64_t)load / 100;
    volatile uint64_t sink = 0;
    while (bench_now_ns() < end) {
        uint64_t window_start = bench_now_ns();
        while (bench_now_ns() - window_start < busy)
            sink++;
        if (busy < WINDOW_NS) {
            struct timespec idle = {0, (long)(WINDOW_NS - busy)};
            nanosleep(&idle, NULL);
        }
    }
    return 0;
}
