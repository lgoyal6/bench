/*
 * L2 broken controller: produces the right commands, but the way a quick first draft
 * often does. It polls on a relative sleep instead of blocking on the tick, and logs and
 * allocates inside the loop, so acknowledgements land up to a full poll interval late.
 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "l2_api.h"

int main(void)
{
    int fd = shm_open(L2_SHM_NAME, O_RDWR, 0);
    if (fd < 0) {
        perror("controller: shm_open");
        return 1;
    }
    l2_shared *sh = mmap(NULL, sizeof *sh, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (sh == MAP_FAILED) {
        perror("controller: mmap");
        return 1;
    }
    atomic_store(&sh->attached, 1);

    unsigned last = 0;
    float command = 0.0f;
    struct timespec poll = {0, 5000000L}; /* 5 ms */
    while (!atomic_load(&sh->done)) {
        unsigned seq = atomic_load(&sh->seq);
        if (seq != last) {
            char *line = malloc(128);
            float target = atomic_load(&sh->target_deg);
            float delta = target - command;
            if (delta > L2_MAX_STEP_DEG) delta = L2_MAX_STEP_DEG;
            if (delta < -L2_MAX_STEP_DEG) delta = -L2_MAX_STEP_DEG;
            command += delta;
            snprintf(line, 128, "tick %u target %.2f command %.2f\n", seq, target, command);
            fputs(line, stdout);
            free(line);
            atomic_store(&sh->command_deg, command);
            atomic_store(&sh->ack_seq, seq);
            last = seq;
        }
        nanosleep(&poll, NULL);
    }
    return 0;
}
