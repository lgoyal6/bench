/* Test-only harness: times a byte round trip through two FIFOs. */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "bench_trace.h"

int main(int argc, char **argv)
{
    const char *log_path = NULL, *ready = NULL;
    int trials = 0;
    unsigned seed = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--log")) log_path = argv[i + 1];
        else if (!strcmp(argv[i], "--ready")) ready = argv[i + 1];
        else if (!strcmp(argv[i], "--trials")) trials = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--seed")) seed = (unsigned)atol(argv[i + 1]);
    }
    if (!log_path || !ready || trials < 1) return 2;
    srand(seed);

    unlink("stim");
    unlink("resp");
    if (mkfifo("stim", 0600) || mkfifo("resp", 0600)) return 1;
    FILE *r = fopen(ready, "w");
    if (!r) return 1;
    fclose(r);

    int stim = open("stim", O_WRONLY); /* blocks until the solution opens it */
    int resp = open("resp", O_RDONLY);
    bench_log log;
    if (stim < 0 || resp < 0 || bench_log_open(&log, log_path)) return 1;

    for (int t = 0; t < trials; t++) {
        struct timespec gap = {0, (5 + rand() % 10) * 1000000L};
        nanosleep(&gap, NULL);
        char c = 'x';
        uint64_t s = bench_now_ns();
        if (write(stim, &c, 1) != 1) break;
        uint64_t got = read(resp, &c, 1) == 1 ? bench_now_ns() : 0;
        bench_log_reaction(&log, t, "echo", s, got, 0);
    }
    bench_log_extra(&log, "echo_trials", trials);
    bench_log_close(&log);
    close(stim);
    close(resp);
    return 0;
}
