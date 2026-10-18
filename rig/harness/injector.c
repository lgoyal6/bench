/*
 * injector: executes a fault schedule on the rig with rig-side timing.
 *
 *   injector --schedule FILE --harness-pid PID --hog-bin PATH --hog-core N
 *
 * Schedule file (written by bench_rig from the shared FaultSchedule), one event per line,
 * sorted by time:
 *   <at_ms> <kind> <target> <duration_ms or -> <load_pct or ->
 *
 * kill_driver    SIGKILL every process named <target>
 * stop_sensor    SIGUSR1 to the task harness, value = duration_ms; the harness stops the stream
 * corrupt_bytes  SIGUSR2 to the task harness, value = duration_ms; the harness corrupts the stream
 * cpu_hog        run cpuhog on the contestant's core for duration_ms at load_pct
 */
#include <ctype.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include "bench_trace.h"

#define MAX_EVENTS 256

typedef struct {
    long at_ms;
    char kind[32];
    char target[64];
    long duration_ms; /* -1 = instantaneous */
    int load_pct;     /* -1 = default */
} event;

static int valid_name(const char *s)
{
    if (!*s)
        return 0;
    for (; *s; s++)
        if (!isalnum((unsigned char)*s) && *s != '_' && *s != '-' && *s != '.')
            return 0;
    return 1;
}

static int parse_schedule(const char *path, event *events, int max)
{
    FILE *f = fopen(path, "r");
    char line[256], dur[32], load[32];
    int n = 0;
    if (!f)
        return -1;
    while (fgets(line, sizeof line, f)) {
        event *e = &events[n];
        if (line[0] == '#' || line[0] == '\n')
            continue;
        if (n == max) {
            fprintf(stderr, "injector: more than %d events\n", max);
            fclose(f);
            return -1;
        }
        if (sscanf(line, "%ld %31s %63s %31s %31s", &e->at_ms, e->kind, e->target, dur, load) != 5) {
            fprintf(stderr, "injector: bad line: %s", line);
            fclose(f);
            return -1;
        }
        if (!valid_name(e->target)) {
            fprintf(stderr, "injector: bad target name: %s\n", e->target);
            fclose(f);
            return -1;
        }
        e->duration_ms = strcmp(dur, "-") ? atol(dur) : -1;
        e->load_pct = strcmp(load, "-") ? atoi(load) : -1;
        n++;
    }
    fclose(f);
    return n;
}

static void sleep_until(uint64_t t_ns)
{
    struct timespec ts = {(time_t)(t_ns / 1000000000ull), (long)(t_ns % 1000000000ull)};
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL) == EINTR)
        ;
}

static int kill_by_name(const char *name)
{
    char cmd[128];
#ifdef __QNXNTO__
    snprintf(cmd, sizeof cmd, "slay -f -s KILL %s", name);
#else
    snprintf(cmd, sizeof cmd, "pkill -KILL -x %s", name);
#endif
    return system(cmd);
}

static int signal_harness(pid_t pid, int sig, long duration_ms)
{
    union sigval v;
    v.sival_int = (int)duration_ms;
    return sigqueue(pid, sig, v);
}

static pid_t spawn_hog(const char *bin, int core, long ms, int load)
{
    char core_s[16], ms_s[24], load_s[16];
    pid_t pid = fork();
    if (pid != 0)
        return pid;
    snprintf(core_s, sizeof core_s, "%d", core);
    snprintf(ms_s, sizeof ms_s, "%ld", ms);
    snprintf(load_s, sizeof load_s, "%d", load);
    execl(bin, bin, "--core", core_s, "--ms", ms_s, "--load", load_s, (char *)NULL);
    perror("injector: exec cpuhog");
    _exit(127);
}

int main(int argc, char **argv)
{
    const char *schedule = NULL, *hog_bin = NULL;
    pid_t harness = 0;
    int hog_core = -1;
    event events[MAX_EVENTS];
    char msg[160];

    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--schedule"))
            schedule = argv[i + 1];
        else if (!strcmp(argv[i], "--harness-pid"))
            harness = (pid_t)atol(argv[i + 1]);
        else if (!strcmp(argv[i], "--hog-bin"))
            hog_bin = argv[i + 1];
        else if (!strcmp(argv[i], "--hog-core"))
            hog_core = atoi(argv[i + 1]);
    }
    if (!schedule || !hog_bin || harness <= 0) {
        fprintf(stderr, "usage: injector --schedule FILE --harness-pid PID --hog-bin PATH --hog-core N\n");
        return 2;
    }

    int n = parse_schedule(schedule, events, MAX_EVENTS);
    if (n < 0)
        return 2;

    uint64_t start = bench_now_ns();
    for (int i = 0; i < n; i++) {
        event *e = &events[i];
        int rc;
        sleep_until(start + (uint64_t)e->at_ms * 1000000ull);

        if (!strcmp(e->kind, "kill_driver")) {
            rc = kill_by_name(e->target);
        } else if (!strcmp(e->kind, "stop_sensor")) {
            rc = signal_harness(harness, SIGUSR1, e->duration_ms < 0 ? 0 : e->duration_ms);
        } else if (!strcmp(e->kind, "corrupt_bytes")) {
            rc = signal_harness(harness, SIGUSR2, e->duration_ms < 0 ? 0 : e->duration_ms);
        } else if (!strcmp(e->kind, "cpu_hog")) {
            long ms = e->duration_ms < 0 ? 1000 : e->duration_ms;
            int load = e->load_pct < 0 ? 100 : e->load_pct;
            rc = spawn_hog(hog_bin, hog_core, ms, load) > 0 ? 0 : -1;
        } else {
            fprintf(stderr, "injector: unknown fault kind %s\n", e->kind);
            return 2;
        }

        snprintf(msg, sizeof msg, "fault %d %s %s at %ld ms rc=%d", i, e->kind, e->target, e->at_ms, rc);
        bench_mark(BENCH_MARK_FAULT, msg);
        printf("%llu %s\n", (unsigned long long)(bench_now_ns() - start), msg);
        fflush(stdout);
    }

    while (wait(NULL) > 0)
        ;
    return 0;
}
