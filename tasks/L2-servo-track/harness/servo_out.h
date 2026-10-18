/*
 * servo_out.h: physical servo output for the L2 harness.
 *
 * BENCH_SERVO unset: timing only, no servo is driven (servo_output=0 in the metrics).
 * BENCH_SERVO set:   drive the servo through that backend. No backend is implemented yet:
 * the Pi 5 PWM/GPIO interface on QNX has to be confirmed on hardware first, so any value
 * fails loudly instead of pretending to move the servo.
 */
#ifndef SERVO_OUT_H
#define SERVO_OUT_H

#include <stdio.h>
#include <stdlib.h>

typedef struct {
    int enabled;
} servo_out;

static inline int servo_out_open(servo_out *s)
{
    const char *backend = getenv("BENCH_SERVO");
    s->enabled = 0;
    if (!backend || !*backend)
        return 0;
    fprintf(stderr, "servo_out: backend '%s' is not implemented yet\n", backend);
    return -1;
}

static inline void servo_out_write(servo_out *s, float deg)
{
    (void)s;
    (void)deg;
}

static inline void servo_out_close(servo_out *s)
{
    (void)s;
}

#endif /* SERVO_OUT_H */
