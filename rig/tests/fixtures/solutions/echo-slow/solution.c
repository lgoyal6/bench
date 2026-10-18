/* Test-only solution: correct output, but 50 ms late every time. */
#define _POSIX_C_SOURCE 200809L
#include <fcntl.h>
#include <time.h>
#include <unistd.h>

int main(void)
{
    int stim = open("stim", O_RDONLY);
    int resp = open("resp", O_WRONLY);
    struct timespec late = {0, 50000000L};
    char c;
    while (read(stim, &c, 1) == 1) {
        nanosleep(&late, NULL);
        if (write(resp, &c, 1) != 1)
            return 1;
    }
    return 0;
}
