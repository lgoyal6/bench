/* L1-head-reflex broken: reacts to every message after a 200 ms pause, and treats HOLD as
 * "look straight ahead", so it misses the deadline and moves during silent rounds. */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <string.h>
#include <time.h>

int main(void)
{
    char line[128];
    struct timespec pause = {0, 200 * 1000 * 1000};
    while (fgets(line, sizeof line, stdin)) {
        unsigned seq;
        float yaw, pitch;
        if (strncmp(line, "END", 3) == 0)
            break;
        nanosleep(&pause, NULL);
        if (sscanf(line, "TARGET %u %f %f", &seq, &yaw, &pitch) == 3)
            printf("MOVE %.1f %.1f\n", yaw, pitch);
        else if (sscanf(line, "HOLD %u", &seq) == 1)
            printf("MOVE 0 20\n");
        fflush(stdout);
    }
    return 0;
}
