/*
 * bench_trace.h: timing log and kernel trace markers shared by every task harness.
 *
 * A task harness generates stimuli and observes responses. It timestamps both on the
 * rig's monotonic clock and writes them to a timing log that bench_rig turns into metrics.
 * On QNX each record is also inserted into the kernel event trace as a user event, so
 * every number in the timing log can be cross-checked against the raw .kev trace.
 *
 * Records are kept in a preallocated memory buffer and written to the file only by
 * bench_log_close(), so a harness never blocks on storage during timed trials. (On the
 * Pi 5 a write to the SD card can stall for tens of milliseconds, longer than a deadline.)
 *
 * Timing log format (one record per line, comma separated):
 *   R,<trial>,<deadline>,<stimulus_ns>,<response_ns or ->,<silent 0|1>   reaction trial
 *   F,<fault_index>,<kind>,<survived 0|1>                                fault outcome
 *   X,<name>,<value>                                                     task-specific metric
 *   D,<completed_ns>                                                    harness completion
 *
 * Reserved extras: rig_overruns (trials the rig started late) and log_overflow.
 */
#ifndef BENCH_TRACE_H
#define BENCH_TRACE_H

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef __QNXNTO__
#include <sys/neutrino.h>
#include <sys/trace.h>
#endif

enum {
    BENCH_MARK_STIMULUS = 1000,
    BENCH_MARK_RESPONSE = 1001,
    BENCH_MARK_FAULT = 1002,
    BENCH_MARK_INFO = 1003,
};

static inline uint64_t bench_now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

/* Insert a user string event into the QNX kernel trace (no-op elsewhere). */
static inline void bench_mark(int code, const char *msg)
{
#ifdef __QNXNTO__
    TraceEvent(_NTO_TRACE_INSERTUSRSTREVENT, code, msg);
#else
    (void)code;
    (void)msg;
#endif
}

#define BENCH_LOG_CAPACITY (8u << 20) /* 8 MiB: about 100k records */

typedef struct {
    FILE *f;
    char *buf;
    size_t len;
    int overflowed;
} bench_log;

/* Opens the file and allocates the buffer up front, touching every page so no page
 * fault or allocation happens during trials. Returns 0 on success. */
static inline int bench_log_open(bench_log *log, const char *path)
{
    log->len = 0;
    log->overflowed = 0;
    log->buf = malloc(BENCH_LOG_CAPACITY);
    if (!log->buf)
        return -1;
    memset(log->buf, 0, BENCH_LOG_CAPACITY);
    log->f = fopen(path, "w");
    if (!log->f) {
        free(log->buf);
        log->buf = NULL;
        return -1;
    }
    return 0;
}

static inline void bench_log_append(bench_log *log, const char *fmt, ...)
{
    va_list ap;
    size_t room = BENCH_LOG_CAPACITY - log->len;
    int n;
    if (log->overflowed)
        return;
    va_start(ap, fmt);
    n = vsnprintf(log->buf + log->len, room, fmt, ap);
    va_end(ap);
    if (n < 0 || (size_t)n >= room)
        log->overflowed = 1;
    else
        log->len += (size_t)n;
}

/* Writes every buffered record and closes the file. An overflow is recorded so the
 * scorer can tell a truncated log from a short run. */
static inline void bench_log_close(bench_log *log)
{
    if (log->f) {
        fwrite(log->buf, 1, log->len, log->f);
        if (log->overflowed)
            fprintf(log->f, "X,log_overflow,1.000000\n");
        fprintf(log->f, "D,%llu\n", (unsigned long long)bench_now_ns());
        fclose(log->f);
        log->f = NULL;
    }
    free(log->buf);
    log->buf = NULL;
}

/* response_ns == 0 means no response was observed before the trial ended. */
static inline void bench_log_reaction(bench_log *log, int trial, const char *deadline,
                                      uint64_t stimulus_ns, uint64_t response_ns, int silent)
{
    char msg[96];
    if (response_ns)
        bench_log_append(log, "R,%d,%s,%llu,%llu,%d\n", trial, deadline,
                         (unsigned long long)stimulus_ns, (unsigned long long)response_ns,
                         silent ? 1 : 0);
    else
        bench_log_append(log, "R,%d,%s,%llu,-,%d\n", trial, deadline,
                         (unsigned long long)stimulus_ns, silent ? 1 : 0);
    snprintf(msg, sizeof msg, "trial %d %s", trial, response_ns ? "responded" : "no response");
    bench_mark(BENCH_MARK_RESPONSE, msg);
}

static inline void bench_log_fault(bench_log *log, int fault_index, const char *kind, int survived)
{
    char msg[96];
    bench_log_append(log, "F,%d,%s,%d\n", fault_index, kind, survived ? 1 : 0);
    snprintf(msg, sizeof msg, "fault %d %s %s", fault_index, kind, survived ? "survived" : "not survived");
    bench_mark(BENCH_MARK_FAULT, msg);
}

static inline void bench_log_extra(bench_log *log, const char *name, double value)
{
    bench_log_append(log, "X,%s,%.6f\n", name, value);
}

/* A trial the rig itself could not start on time (the harness woke up past its
 * schedule). Harnesses still record the trial (timed from the actual stimulus), mark it
 * in the kernel trace here, and report the count as the extra metric "rig_overruns". Any
 * overrun makes the run an "error", so the rig is fixed rather than a contestant blamed. */
static inline void bench_mark_rig_overrun(int trial)
{
    char msg[64];
    snprintf(msg, sizeof msg, "rig overrun at trial %d", trial);
    bench_mark(BENCH_MARK_INFO, msg);
}

#endif /* BENCH_TRACE_H */
