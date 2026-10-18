/*
 * schedprobe: sets known thread priorities so a kernel trace of it checks the rule audit.
 *
 *   schedprobe [PRIO ...]          default: 20 45 55
 *
 * For each priority, calls pthread_setschedparam(SCHED_FIFO) (SchedSet on QNX), then sleeps
 * 10 ms so each call is separate in the trace. Prints one JSON object with each requested
 * priority and its result (0 = success). Run it under tracelogger and audit the trace with
 * process name "schedprobe": the audit's priorities_set must equal the successful requests.
 */
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

int main(int argc, char **argv)
{
    int defaults[] = {20, 45, 55};
    int count = argc > 1 ? argc - 1 : 3;
    printf("{\"calls\": [");
    for (int i = 0; i < count; i++) {
        struct sched_param param = {.sched_priority = argc > 1 ? atoi(argv[i + 1]) : defaults[i]};
        int rc = pthread_setschedparam(pthread_self(), SCHED_FIFO, &param);
        printf("%s{\"priority\": %d, \"rc\": %d}", i ? ", " : "", param.sched_priority, rc);
        struct timespec pause = {0, 10 * 1000 * 1000};
        nanosleep(&pause, NULL);
    }
    printf("]}\n");
    return 0;
}
