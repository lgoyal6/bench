/*
 * rtlatency: cyclictest-style wakeup latency measurement, used to calibrate a rig.
 *
 *   rtlatency [--period-us 1000] [--seconds 10] [--core N] [--prio P]
 *
 * A thread sleeps until absolute deadlines one period apart and records how late each
 * wakeup is. Prints one JSON object with the clock resolution, min, avg, p99 and max
 * latency, and jitter (max - min), in microseconds.
 * Run it on the contestant core, idle and under cpu_hog on the other cores, to publish
 * the rig's baseline before any contestant is scored.
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bench_pin.h"
#include "bench_trace.h"

static int cmp_u64(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return x < y ? -1 : x > y;
}

int main(int argc, char **argv)
{
    long period_us = 1000, seconds = 10;
    int core = -1, prio = 0;

    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--period-us"))
            period_us = atol(argv[i + 1]);
        else if (!strcmp(argv[i], "--seconds"))
            seconds = atol(argv[i + 1]);
        else if (!strcmp(argv[i], "--core"))
            core = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--prio"))
            prio = atoi(argv[i + 1]);
    }
    if (period_us < 50 || seconds < 1) {
        fprintf(stderr, "usage: rtlatency [--period-us >=50] [--seconds >=1] [--core N] [--prio P]\n");
        return 2;
    }
    if (bench_pin_core(core) != 0) {
        perror("rtlatency: pin core");
        return 1;
    }
    if (bench_set_fifo(prio) != 0) {
        fprintf(stderr, "rtlatency: could not set priority %d\n", prio);
        return 1;
    }

    size_t n = (size_t)(seconds * 1000000 / period_us);
    uint64_t *lat = malloc(n * sizeof *lat);
    if (!lat) {
        perror("rtlatency: malloc");
        return 1;
    }

    uint64_t period_ns = (uint64_t)period_us * 1000ull;
    uint64_t next = bench_now_ns() + period_ns;
    for (size_t i = 0; i < n; i++) {
        struct timespec ts = {(time_t)(next / 1000000000ull), (long)(next % 1000000000ull)};
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL) == EINTR)
            ;
        uint64_t now = bench_now_ns();
        lat[i] = now > next ? now - next : 0;
        next += period_ns;
    }

    double sum = 0;
    for (size_t i = 0; i < n; i++)
        sum += (double)lat[i];
    qsort(lat, n, sizeof *lat, cmp_u64);
    size_t p99 = (size_t)((double)n * 0.99);
    if (p99 >= n)
        p99 = n - 1;

    /* Absolute lateness includes the clock tick (a sleep ends on the first tick after
     * its deadline); jitter, max minus min, is the part that reflects the rig. */
    printf("{\"samples\": %zu, \"period_us\": %ld, \"core\": %d, \"prio\": %d, "
           "\"clock_res_us\": %.3f, \"min_us\": %.3f, \"avg_us\": %.3f, \"p99_us\": %.3f, "
           "\"max_us\": %.3f, \"jitter_us\": %.3f}\n",
           n, period_us, core, prio, bench_clock_resolution_ns() / 1000.0, lat[0] / 1000.0,
           sum / (double)n / 1000.0, lat[p99] / 1000.0, lat[n - 1] / 1000.0,
           (lat[n - 1] - lat[0]) / 1000.0);
    free(lat);
    return 0;
}
