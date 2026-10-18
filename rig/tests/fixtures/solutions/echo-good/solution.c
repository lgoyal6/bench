/* Test-only solution: echoes immediately. */
#include <fcntl.h>
#include <unistd.h>

int main(void)
{
    int stim = open("stim", O_RDONLY);
    int resp = open("resp", O_WRONLY);
    char c;
    while (read(stim, &c, 1) == 1)
        if (write(resp, &c, 1) != 1)
            return 1;
    return 0;
}
