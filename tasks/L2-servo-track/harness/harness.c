/*
 * L2 harness: 100 Hz servo target stream, timed on the rig.
 *
 *   harness --seed S --trials N --log PATH --ready PATH
 *
 * Each tick the harness publishes a target angle from a seeded smooth trajectory and
 * spins on its own core until the controller acknowledges that tick or the next tick
 * starts. Ticks acknowledged within L2_DEADLINE_MS are hits. It then drives the servo
 * with the controller's command when a servo output is configured.
 *
 * Extra metrics: angle_error_deg (mean |command - target| over acknowledged ticks),
 * missed_ticks, servo_output (1 if a physical servo was driven, 0 if timing only).
 */
#include <fcntl.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "bench_trace.h"
#include "l2_api.h"
#include "servo_out.h"

#define ATTACH_TIMEOUT_MS 10000
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static double trajectory(double t, const double *freq, const double *phase)
{
    /* Sum of three sines, scaled to stay inside the servo's range. */
    double v = 0.5 * sin(2 * M_PI * freq[0] * t + phase[0])
             + 0.3 * sin(2 * M_PI * freq[1] * t + phase[1])
             + 0.2 * sin(2 * M_PI * freq[2] * t + phase[2]);
    return v * 0.9 * L2_MAX_DEG;
}

static float expected_command(float previous, float target)
{
    float delta = target - previous;
    if (delta > L2_MAX_STEP_DEG) delta = L2_MAX_STEP_DEG;
    if (delta < -L2_MAX_STEP_DEG) delta = -L2_MAX_STEP_DEG;
    float next = previous + delta;
    if (next > L2_MAX_DEG) next = L2_MAX_DEG;
    if (next < L2_MIN_DEG) next = L2_MIN_DEG;
    return next;
}

static void sleep_until(uint64_t t_ns)
{
    struct timespec ts = {(time_t)(t_ns / 1000000000ull), (long)(t_ns % 1000000000ull)};
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL) != 0)
        ;
}

int main(int argc, char **argv)
{
    const char *log_path = NULL, *ready = NULL;
    int trials = 0;
    unsigned seed = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--log")) log_path = argv[i + 1];
        else if (!strcmp(argv[i], "--ready")) ready = argv[i + 1];
        else if (!strcmp(argv[i], "--trials")) trials = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--seed")) seed = (unsigned)strtoul(argv[i + 1], NULL, 10);
    }
    if (!log_path || !ready || trials < 1) {
        fprintf(stderr, "usage: harness --seed S --trials N --log PATH --ready PATH\n");
        return 2;
    }

    /* This task has no sensor stream; ignore the injector's sensor signals. */
    signal(SIGUSR1, SIG_IGN);
    signal(SIGUSR2, SIG_IGN);

    srand(seed);
    double freq[3], phase[3];
    for (int k = 0; k < 3; k++) {
        freq[k] = 0.1 + (rand() % 1000) / 1000.0 * (0.4 + 0.3 * k);
        phase[k] = (rand() % 6283) / 1000.0;
    }

    shm_unlink(L2_SHM_NAME);
    int fd = shm_open(L2_SHM_NAME, O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd < 0 || ftruncate(fd, sizeof(l2_shared)) != 0) {
        perror("harness: shm");
        return 1;
    }
    l2_shared *sh = mmap(NULL, sizeof *sh, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (sh == MAP_FAILED) {
        perror("harness: mmap");
        return 1;
    }
    memset(sh, 0, sizeof *sh);
    if (sem_init(&sh->tick, 1, 0) != 0) {
        perror("harness: sem_init");
        return 1;
    }

    servo_out servo;
    if (servo_out_open(&servo) != 0) {
        fprintf(stderr, "harness: servo output configured but could not be opened\n");
        return 1;
    }

    bench_log log;
    if (bench_log_open(&log, log_path) != 0) {
        perror("harness: log");
        return 1;
    }
    FILE *r = fopen(ready, "w");
    if (!r) {
        perror("harness: ready");
        return 1;
    }
    fclose(r);

    uint64_t attach_deadline = bench_now_ns() + ATTACH_TIMEOUT_MS * 1000000ull;
    while (!atomic_load(&sh->attached) && bench_now_ns() < attach_deadline) {
        struct timespec poll = {0, 1000000L};
        nanosleep(&poll, NULL);
    }
    if (!atomic_load(&sh->attached))
        bench_mark(BENCH_MARK_INFO, "controller never attached");

    uint64_t period = (uint64_t)L2_PERIOD_MS * 1000000ull;
    uint64_t start = bench_now_ns() + period;
    double error_sum = 0;
    int acked = 0, missed = 0, invalid_commands = 0, rig_overruns = 0;
    float previous_command = 0.0f;
    /* A sleep ends on the first clock tick after its deadline, so every wakeup can be up
     * to one tick late (1 ms on the Pi 5 QNX image). Waking later than that plus half a
     * deadline means the rig, not the controller, lost time. */
    uint64_t overrun_ns = bench_clock_resolution_ns() + (uint64_t)L2_DEADLINE_MS * 1000000ull / 2;

    for (int t = 0; t < trials; t++) {
        uint64_t tick_at = start + (uint64_t)t * period;
        sleep_until(tick_at);
        if (bench_now_ns() - tick_at > overrun_ns) {
            rig_overruns++;
            bench_mark_rig_overrun(t);
        }

        float target = (float)trajectory((double)t * L2_PERIOD_MS / 1000.0, freq, phase);
        atomic_store(&sh->target_deg, target);
        atomic_store(&sh->seq, (unsigned)t + 1);
        uint64_t published = bench_now_ns();
        bench_mark(BENCH_MARK_STIMULUS, "tick");
        sem_post(&sh->tick);

        /* Spin until acknowledged or the next tick is due. */
        uint64_t response = 0, next_tick = tick_at + period;
        for (;;) {
            if (atomic_load(&sh->ack_seq) == (unsigned)t + 1) {
                response = bench_now_ns();
                break;
            }
            if (bench_now_ns() >= next_tick - 200000ull) /* leave 0.2 ms to log and sleep */
                break;
        }
        float cmd = 0.0f;
        if (response) {
            cmd = atomic_load(&sh->command_deg);
            float expected = expected_command(previous_command, target);
            if (!isfinite(cmd) || cmd < L2_MIN_DEG || cmd > L2_MAX_DEG ||
                fabsf(cmd - expected) > 0.0001f) {
                invalid_commands++;
                /* An acknowledgment without the required command is not a valid response. */
                response = 0;
            }
        }
        bench_log_reaction(&log, t, "tick_ack", published, response, 0);

        if (response) {
            previous_command = cmd;
            error_sum += fabs((double)cmd - (double)target);
            acked++;
            servo_out_write(&servo, cmd);
        } else {
            missed++;
        }
    }

    atomic_store(&sh->done, 1);
    sem_post(&sh->tick);

    bench_log_extra(&log, "angle_error_deg", acked ? error_sum / acked : 0.0);
    bench_log_extra(&log, "missed_ticks", missed);
    bench_log_extra(&log, "invalid_commands", invalid_commands);
    bench_log_extra(&log, "servo_output", servo.enabled ? 1 : 0);
    bench_log_extra(&log, "rig_overruns", rig_overruns);
    bench_log_close(&log);
    servo_out_close(&servo);
    munmap(sh, sizeof *sh);
    shm_unlink(L2_SHM_NAME);
    return 0;
}
