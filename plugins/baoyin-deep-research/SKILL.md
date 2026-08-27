---
name: baoyin-deep-research
description: Use for research, intelligence, comparison, or fact-check tasks requiring external sources. Enforces a fixed order: recall local knowledge first, then search, cross-validate from multiple independent sources, and report conclusions with evidence and sources. Do not use for direct engineering changes or short factual Q&A already covered by local knowledge.
---

# Deep Research Skill

A disciplined research workflow for intelligence, industry comparison, and fact-checking tasks.

## When to trigger
- Research / intelligence / industry comparison / fact-check / solution pre-study.
- User asks to "search first / verify first / gather material first".

## Fixed order (no skipping)
1. **Recall**: search local knowledge/memory first to check whether a conclusion already exists.
2. **Research**: search the web (meta-search or official docs/GitHub); cross-verify when necessary; record a source URL for each conclusion.
3. **Cross-validate**: require at least 2 independent sources for the same topic; mark low-coverage items as "unverified" rather than inventing facts.
4. **Output**: give conclusion / evidence / sources / unverified points / next steps. Do not present a raw list of search results as the research conclusion.
5. **Persist**: write key conclusions back to long-term memory.

## Principles
- Prefer local/internal knowledge before external search.
- Never output a conclusion from memory alone; record sources.
- Mark what is unverified; do not fabricate evidence.
