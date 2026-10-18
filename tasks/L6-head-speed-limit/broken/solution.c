/* L6-head-speed-limit broken: snaps straight to each target (fine for L1), so the servos
 * move at 300+ deg/s and break the 100 deg/s envelope. */
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
