/*
 * L6-head-speed-limit reference: ramp the commanded pose toward the target at 60 deg/s,
 * sending one MOVE every 20 ms, while still reading new messages without blocking.
 */
#define _POSIX_C_SOURCE 200809L
#include <math.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define SPEED_DPS 60.0 /* well under the 100 deg/s limit */
#define TICK_MS 20     /* 50 MOVE lines per second while moving */

static double now_s(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(void)
{
    double yaw = 0, pitch = 10;            /* the harness starts the head here */
    double target_yaw = yaw, target_pitch = pitch;
    int moving = 0;
    double last_step = now_s();
    char buf[4096];
    size_t len = 0;

    for (;;) {
        struct pollfd in = {.fd = STDIN_FILENO, .events = POLLIN};
        int ready = poll(&in, 1, moving ? TICK_MS : -1);
        if (ready > 0) {
            ssize_t n = read(STDIN_FILENO, buf + len, sizeof buf - 1 - len);
            if (n <= 0)
                return 0;
            len += (size_t)n;
            buf[len] = 0;
            char *line = buf, *nl;
            while ((nl = strchr(line, '\n'))) {
                *nl = 0;
                unsigned seq;
                float y, p;
                if (sscanf(line, "TARGET %u %f %f", &seq, &y, &p) == 3) {
                    target_yaw = y;
                    target_pitch = p;
                    if (!moving) /* first step goes out at once */
                        last_step = now_s() - TICK_MS / 1000.0;
                    moving = 1;
                } else if (strncmp(line, "END", 3) == 0) {
                    return 0;
                }
                line = nl + 1;
            }
            len = strlen(line);
            memmove(buf, line, len + 1);
        }
        if (!moving)
            continue;
        double t = now_s(), dt = t - last_step;
        if (dt < TICK_MS / 1000.0)
            continue;
        if (dt > 2 * TICK_MS / 1000.0)
            dt = 2 * TICK_MS / 1000.0;
        last_step = t;
        double dy = target_yaw - yaw, dp = target_pitch - pitch;
        double dist = hypot(dy, dp), step = SPEED_DPS * dt;
        if (dist <= step) {
            yaw = target_yaw;
            pitch = target_pitch;
            moving = 0;
        } else {
            yaw += dy * step / dist;
            pitch += dp * step / dist;
        }
        printf("MOVE %.2f %.2f\n", yaw, pitch);
        fflush(stdout);
    }
}
