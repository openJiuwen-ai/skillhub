---
name: baoyin-knowledge-distill
description: Use at the end of a non-trivial task to distill conclusions, evidence, and next actions into durable memory and a knowledge base. Enforces: write the evidence, persist to memory and knowledge notes, then verify the files exist. Do not use for pure chat replies without durable output.
---

# Knowledge Distill Skill

A skill for persisting durable conclusions, evidence, and next actions at the end of a task.

## When to trigger
- A task produced a conclusion / solution / lesson / verification result worth keeping.
- User asks to "persist / record / file it".

## Steps
1. **Write the evidence**: one-line conclusion + verification command/log/API result + file path.
2. **Persist**:
   - Write the conclusion to long-term memory.
   - Append to the daily knowledge note.
   - Add a dedicated topic page if needed.
3. **Sync**: mirror the notes to the knowledge vault.
4. **Verify**: confirm the file exists and contains conclusion / evidence / timestamp. Without evidence, the task is not complete.

## Principles
- Persist reusable conclusions, not raw task context.
- Every entry carries evidence; no evidence = not done.
- Never persist secrets or tokens.
