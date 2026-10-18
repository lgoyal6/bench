"""Rig rules every contestant must follow, checked against the kernel trace of each run.

The same constants feed the task brief contestants receive and the audit the rig runs, so
the rule an agent is told and the rule it is judged by cannot drift apart.
"""

# The harness that measures contestants runs at priority 60; contestants stay below it.
CONTESTANT_MAX_PRIORITY = 50

RULES_TEXT = f"""Rig rules (checked against the QNX kernel trace of every run; breaking one fails the run):
- Do not change your CPU affinity (runmask). The rig pins your program to its own core.
- You may set your threads' scheduling policy and priority, but never above priority {CONTESTANT_MAX_PRIORITY}."""
