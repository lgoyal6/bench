/*
 * bench_pin.h: pin the calling thread to one CPU core and set a real-time priority.
 *
 * QNX OS 8.0 has no adaptive partitioning, so Bench isolates runs by core instead:
 * the contestant gets a core of its own and rig processes stay off it.
 */
#ifndef BENCH_PIN_H
#define BENCH_PIN_H

#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <string.h>

#ifdef __QNXNTO__
#include <sys/neutrino.h>
#endif

/* Returns 0 on success, -1 if the core could not be set. */
static inline int bench_pin_core(int core)
{
    if (core < 0)
        return 0;
#ifdef __QNXNTO__
    if (ThreadCtl(_NTO_TCTL_RUNMASK, (void *)(uintptr_t)(1u << core)) == -1)
        return -1;
    return 0;
#elif defined(__linux__)
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(core, &set);
    return sched_setaffinity(0, sizeof set, &set);
#else
    return -1;
#endif
}

/* SCHED_FIFO at the given priority. Returns 0 on success. priority <= 0 leaves it alone. */
static inline int bench_set_fifo(int priority)
{
    struct sched_param sp;
    if (priority <= 0)
        return 0;
    memset(&sp, 0, sizeof sp);
    sp.sched_priority = priority;
    return pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
}

#endif /* BENCH_PIN_H */
