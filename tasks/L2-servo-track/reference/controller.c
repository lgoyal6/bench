/*
 * L2 reference controller: block on the tick semaphore, compute a slew-limited command,
 * publish it, acknowledge. Nothing allocates, prints or sleeps inside the loop.
 */
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

#include "l2_api.h"

static float step_toward(float current, float target)
{
    float delta = target - current;
    if (delta > L2_MAX_STEP_DEG) delta = L2_MAX_STEP_DEG;
    if (delta < -L2_MAX_STEP_DEG) delta = -L2_MAX_STEP_DEG;
    float next = current + delta;
    if (next > L2_MAX_DEG) next = L2_MAX_DEG;
    if (next < L2_MIN_DEG) next = L2_MIN_DEG;
    return next;
}

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
    mlockall(MCL_CURRENT | MCL_FUTURE);
    atomic_store(&sh->attached, 1);

    float command = 0.0f;
    while (!atomic_load(&sh->done)) {
        if (sem_wait(&sh->tick) != 0)
            continue;
        if (atomic_load(&sh->done))
            break;
        unsigned seq = atomic_load(&sh->seq);
        command = step_toward(command, atomic_load(&sh->target_deg));
        atomic_store(&sh->command_deg, command);
        atomic_store(&sh->ack_seq, seq);
    }
    return 0;
}
