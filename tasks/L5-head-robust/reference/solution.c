/*
 * L5-head-robust reference: accept only exact, in-range lines; ignore everything else.
 * Lines are read whole however long they are, so a long junk line cannot split into
 * something that looks valid.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* A plain finite decimal: optional sign, digits, optional fraction. Rejects nan, inf, hex,
 * exponents and empty strings. */
static int parse_decimal(const char *s, double *out)
{
    const char *p = s;
    if (*p == '+' || *p == '-')
        p++;
    int digits = 0;
    while (*p >= '0' && *p <= '9')
        p++, digits++;
    if (*p == '.') {
        p++;
        while (*p >= '0' && *p <= '9')
            p++, digits++;
    }
    if (!digits || *p)
        return 0;
    *out = strtod(s, NULL);
    return 1;
}

static int parse_seq(const char *s)
{
    if (!*s)
        return 0;
    for (const char *p = s; *p; p++)
        if (*p < '0' || *p > '9')
            return 0;
    return atol(s) > 0;
}

static void handle(char *line)
{
    char *field[5];
    int n = 0;
    for (char *tok = strtok(line, " "); tok && n < 5; tok = strtok(NULL, " "))
        field[n++] = tok;
    double yaw, pitch;
    if (n == 4 && !strcmp(field[0], "TARGET") && parse_seq(field[1]) && parse_decimal(field[2], &yaw) &&
        parse_decimal(field[3], &pitch) && yaw >= -40 && yaw <= 40 && pitch >= 8 && pitch <= 35) {
        printf("MOVE %.2f %.2f\n", yaw, pitch);
        fflush(stdout);
    } else if (n == 1 && !strcmp(field[0], "END")) {
        exit(0);
    }
    /* HOLD and every invalid line: do nothing */
}

int main(void)
{
    char *line = NULL;
    size_t cap = 0;
    ssize_t len;
    while ((len = getline(&line, &cap, stdin)) >= 0) {
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r'))
            line[--len] = 0;
        handle(line);
    }
    free(line);
    return 0;
}
