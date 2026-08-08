# Coding-agent execution workflow

Use this protocol when implementing `AI_IMPLEMENTATION_PLAN.md`. A **step** means one numbered work package. Complete only the step the user requests, including its directly necessary tests and documentation; do not automatically begin the next package.

## Before starting a step

1. Read the requested work package, `PIPELINE_CONTRACT.md`, relevant risks, and the current `IMPLEMENTATION_LOG.md`.
2. Inspect current repository state and preserve unrelated user changes.
3. Check that prerequisite packages are complete and that required owner decisions are recorded.
4. State briefly in chat what package is starting and any assumptions. Ask only when a missing decision would materially change the implementation.
5. Change the package status in `IMPLEMENTATION_LOG.md` to `IN PROGRESS`.

## While implementing

- Keep changes limited to the requested package and its tests/docs.
- Prefer the smallest change that satisfies the contract.
- Run focused checks during development rather than waiting until the end.
- Do not deploy or mutate production resources unless the user explicitly authorizes it.
- Preserve backward compatibility when the package requires it, such as keeping existing CLI flags.

### Handling an unforeseen issue

If the issue has a small, necessary fix within the package, make the fix and record the deviation in the log. If it changes the architecture, expands scope materially, requires a destructive/external action, or invalidates a prior decision, stop and ask the user before proceeding.

Do not hide deviations by rewriting the original plan. The plan records intent; the implementation log records what actually happened and why.

## Definition of complete for one step

A package is complete only when:

1. Its requested code/config/documentation is implemented.
2. Focused automated checks pass, or unavailable checks and reasons are recorded.
3. The agent has inspected the resulting diff for unrelated changes and secret exposure.
4. `IMPLEMENTATION_LOG.md` contains files changed, behavior changed, tests/results, deviations, decisions, and any remaining follow-up.
5. The agent provides user-runnable verification instructions with expected results.

If implementation is incomplete or verification cannot be performed, mark the package `BLOCKED` or leave it `IN PROGRESS`; do not label it complete.

## Required chat handoff after each step

Use this compact structure:

```text
Package completed: <number and name>

What changed
- High-level behavior and important files; avoid a line-by-line dump.

Verification performed
- Commands/tests run by the agent and their results.

How you can test it
1. Exact command or manual action.
2. Expected successful output/behavior.
3. A useful failure check when applicable.

Deviations or decisions
- Differences from the plan and why, or "None."

Next step
- Name the next package, but do not start it.
```

Testing instructions must be safe and proportionate. Clearly label commands that need Google Cloud credentials, incur cost, deploy resources, or mutate development/production data. Prefer local/read-only checks first. Never ask the user to paste secret values into chat or committed files.

## Progress-log rules

- Update the summary table for every status change.
- Append one dated entry per attempt; do not erase earlier failures or deviations.
- Reference exact repository-relative file paths.
- Record commands and short results, not enormous raw logs.
- Distinguish tests the agent ran from tests the user still needs to run.
- Record external actions separately because a repository edit does not prove deployment occurred.
- If later work changes a completed package, add a new note to both affected package entries.

