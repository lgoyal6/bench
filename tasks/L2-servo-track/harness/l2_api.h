/*
 * l2_api.h: the interface between the L2 harness and a contestant's servo controller.
 *
 * The harness owns the shared memory object L2_SHM_NAME. Every tick (100 Hz) it writes a
 * new target angle, increments seq and posts the `tick` semaphore. The controller must
 * write its servo command and then set ack_seq = seq within L2_DEADLINE_MS of the post.
 * The harness timestamps the moment it sees ack_seq change, on its own core, so the
 * controller cannot report its own timing. When `done` becomes 1 the controller exits.
 */
#ifndef L2_API_H
#define L2_API_H

#include <semaphore.h>
#include <stdatomic.h>
#include <stdint.h>

#define L2_SHM_NAME "/bench_l2"
#define L2_PERIOD_MS 10
#define L2_DEADLINE_MS 2
#define L2_MIN_DEG (-60.0f)
#define L2_MAX_DEG 60.0f
#define L2_MAX_STEP_DEG 3.0f /* largest command change per tick the servo can follow */

typedef struct {
    sem_t tick;                 /* posted by the harness once per tick */
    atomic_uint seq;            /* tick number, written by the harness before posting */
    _Atomic float target_deg;   /* where the servo should point this tick */
    _Atomic float command_deg;  /* written by the controller */
    atomic_uint ack_seq;        /* written by the controller after command_deg */
    atomic_uint attached;       /* controller sets 1 once it has mapped the object */
    atomic_uint done;           /* harness sets 1 at the end of the run */
} l2_shared;

#endif /* L2_API_H */
