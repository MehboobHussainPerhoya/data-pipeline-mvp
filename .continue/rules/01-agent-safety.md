---
name: Agent Safety and Progress Rules
alwaysApply: true
---

# Agent Execution Rules

## 1. Never enter retry loops

- Never repeat the same tool call more than 2 times.
- If a tool call fails twice, STOP retrying that operation.
- Do not make repeated attempts with trivial changes to the same arguments.
- Do not repeatedly say "let me try again" without changing the strategy.

## 2. File creation and editing

When creating or modifying a file:

1. Attempt the operation.
2. Inspect the tool result.
3. If it succeeds, verify the file exists or contains the intended change.
4. If it fails, diagnose the actual error.
5. If the same operation fails a second time, switch to another method.

Never repeatedly call the same file-creation/edit tool after failure.

## 3. Maintain forward progress

- Every tool call must have a clear purpose.
- Do not get stuck trying to satisfy one small task indefinitely.
- If one approach is blocked, use another available tool or method.
- If no viable method exists, stop and report the blocker clearly.

## 4. Terminal fallback

If the file-editing tool fails repeatedly, use the terminal/shell to create or modify the file when appropriate.

After using the terminal, verify the result before continuing.

## 5. Testing

When a test fails:

- Inspect the failure.
- Determine whether the failure is caused by code, test, environment, dependency, or configuration.
- Fix the identified cause.
- Do not blindly rerun the same failing command repeatedly.

Maximum: 2 consecutive attempts without a meaningful change.

## 6. Tool-call discipline

Before every tool call, determine:

- What am I trying to accomplish?
- Has this exact operation already failed?
- What changed since the previous attempt?
- If it fails again, what is my fallback?

## 7. Hard stop condition

If the same operation has failed twice:

STOP.

Do not call the same tool again for that operation.

Instead:

1. Explain the failure.
2. Choose a different approach.
3. Or ask the user for intervention.

## 8. Do not simulate success

Never claim that a file was created, modified, tested, or executed unless the tool result confirms it.

## 9. Continue the larger task

Do not let one blocked operation prevent progress on unrelated parts of the task.

After resolving or reporting a blocker, continue with the remaining work.