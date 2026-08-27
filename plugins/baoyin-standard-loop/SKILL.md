---
name: baoyin-standard-loop
description: A production-grade autonomous agent workflow loop for non-trivial tasks. Enforces recall memory -> research before planning -> plan -> execute in small steps -> verify with independent evidence -> persist -> report. Use for all non-trivial tasks in an AI-native digital company operating 24/7. Do not use for trivial one-liners.
---

# Baoyin Standard Loop

The Baoyin standard task loop for all non-trivial tasks. Prevents "act first, no verification, no persistence" behavior.

## When to trigger
- Any task involving servers, memory, knowledge, planning, search, new features/services, or cross-system changes.
- User explicitly says "sort out / research / plan / verify / wrap up".
- Simple Q&A, one-line translation, pure explanation are NOT covered; do not force the loop.

## Loop steps
1. **Recall (check memory/knowledge)**
   - Review memory summaries, design docs, and relevant knowledge notes.
   - Search both internal notes and long-term memory first; check for prior conclusions before acting.
2. **Research (search before deciding)**
   - For uncertain external facts, search local meta-search or official docs/GitHub first.
   - Record sources; never output conclusions from memory alone.
3. **Plan**
   - List goals, constraints, steps, risks, resource costs, rollback options.
   - For large cross-service changes, leave a checkpoint for the user/long-term plan.
4. **Execute**
   - Execute in small steps; production actions default to dry-run; archive before delete/disable.
5. **Verify**
   - Self-check: command rc, logs, diff, ports/services, acceptance checklist.
   - For important changes, cross-check with an independent command/script, not just your own output.
6. **Persist**
   - Update knowledge notes and task status docs; write key conclusions to long-term memory.
7. **Report**
   - Tell the user: status, what was done, evidence, impact, next steps; do not present process logs as results.

## Principles
- AI-native: check knowledge/memory first, then research, then plan and execute in small steps; automate with scripts/0-token solutions rather than re-running models.
- Autonomous evolution loop: accumulate experience, persist it, optimize skills, and do weekly retrospectives; errors also follow "evidence -> root cause -> record -> prevent recurrence".
