/*
 * bench_trace.h: timing log and kernel trace markers shared by every task harness.
 *
 * A task harness generates stimuli and observes responses. It timestamps both on the
 * rig's monotonic clock and writes them to a timing log that bench_rig turns into metrics.
 * On QNX each record is also inserted into the kernel event trace as a user event, so
 * every number in the timing log can be cross-checked against the raw .kev trace.
 *
 * Timing log format (one record per line, comma separated):
 *   R,<trial>,<deadline>,<stimulus_ns>,<response_ns or ->,<silent 0|1>   reaction trial
 *   F,<fault_index>,<kind>,<survived 0|1>                                fault outcome
 *   X,<name>,<value>                                                     task-specific metric
 *   D,<completed_ns>                                                    harness completion
 */
#ifndef BENCH_TRACE_H
#define BENCH_TRACE_H

#include <stdint.h>
#include <stdio.h>
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

typedef struct {
    FILE *f;
} bench_log;

static inline int bench_log_open(bench_log *log, const char *path)
{
    log->f = fopen(path, "w");
    return log->f ? 0 : -1;
}

static inline void bench_log_close(bench_log *log)
{
    if (log->f) {
        fprintf(log->f, "D,%llu\n", (unsigned long long)bench_now_ns());
        fclose(log->f);
        log->f = NULL;
    }
}

/* response_ns == 0 means no response was observed before the trial ended. */
static inline void bench_log_reaction(bench_log *log, int trial, const char *deadline,
                                      uint64_t stimulus_ns, uint64_t response_ns, int silent)
{
    char msg[96];
    if (response_ns)
        fprintf(log->f, "R,%d,%s,%llu,%llu,%d\n", trial, deadline,
                (unsigned long long)stimulus_ns, (unsigned long long)response_ns, silent ? 1 : 0);
    else
        fprintf(log->f, "R,%d,%s,%llu,-,%d\n", trial, deadline,
                (unsigned long long)stimulus_ns, silent ? 1 : 0);
    snprintf(msg, sizeof msg, "trial %d %s", trial, response_ns ? "responded" : "no response");
    bench_mark(BENCH_MARK_RESPONSE, msg);
}

static inline void bench_log_fault(bench_log *log, int fault_index, const char *kind, int survived)
{
    char msg[96];
    fprintf(log->f, "F,%d,%s,%d\n", fault_index, kind, survived ? 1 : 0);
    snprintf(msg, sizeof msg, "fault %d %s %s", fault_index, kind, survived ? "survived" : "not survived");
    bench_mark(BENCH_MARK_FAULT, msg);
}

static inline void bench_log_extra(bench_log *log, const char *name, double value)
{
    fprintf(log->f, "X,%s,%.6f\n", name, value);
}

#endif /* BENCH_TRACE_H */
