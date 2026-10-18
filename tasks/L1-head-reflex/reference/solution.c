/* L1-head-reflex reference: forward every TARGET as a MOVE at once, ignore HOLD. */
#include <stdio.h>
#include <string.h>

int main(void)
{
    char line[128];
    while (fgets(line, sizeof line, stdin)) {
        unsigned seq;
        float yaw, pitch;
        if (sscanf(line, "TARGET %u %f %f", &seq, &yaw, &pitch) == 3) {
            printf("MOVE %.1f %.1f\n", yaw, pitch);
            fflush(stdout);
        } else if (strncmp(line, "END", 3) == 0) {
            break;
        }
    }
    return 0;
}
