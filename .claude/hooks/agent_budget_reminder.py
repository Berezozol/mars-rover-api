"""PreToolUse hook for the Agent tool: remind the lead about the /budget rule.

CLAUDE.md caps every architect or data-engineer run at 5% of the weekly limit and
asks the lead to log the weekly percentage before and after each run. When such an
agent is about to start, this hook adds a reminder to the model's context. It never
blocks the call and prints nothing for other agents.
"""

import json
import sys

BUDGETED_AGENTS = {"architect", "data-engineer"}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    agent = (payload.get("tool_input") or {}).get("subagent_type") or ""
    if agent not in BUDGETED_AGENTS:
        return 0
    reminder = (
        f"Budget rule (CLAUDE.md): each {agent} run may use at most 5% of the weekly limit. "
        f"If you have not yet, run /budget before {agent} <task> now (weekly % before the run); "
        "after its report arrives, run /budget after to log the difference."
    )
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": reminder,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
