# Repository Architecture

## Overview

The repository is built around a two-stage system:

1. `indexing/` builds structured offline artifacts from skill directories
2. `retrieval/` and `orchestration/` consume those artifacts online to retrieve and execute the right capability

The important architectural split is:

- offline organization of capabilities
- online routing and execution over that organized capability space

There is also an explicit packaging split:

- `indexing/`, `retrieval/`, `orchestration/`, `models/`, `shared/`, and `serving/` form the SDK codebase
- `demo/` and `training/` are consumers of that SDK
- `data/`, `tests/`, and `scripts/` support local development and are not SDK runtime dependencies

## Main Flow

### 1. Offline indexing

Input:

- skill directories
- skill metadata
- optional LLM support for tree construction

Output:

- `tree_index.yaml`
- `catalog.jsonl`
- `embedding_records.jsonl`
- `embedding_index.json`
- `bm25_index.json`
- `manifest.json`

Ownership:

- [indexing/tree](./indexing/tree)
- [indexing/catalog](./indexing/catalog)
- [indexing/embedding](./indexing/embedding)
- [indexing/bm25](./indexing/bm25)
- [indexing/workflows](./indexing/workflows)

### 2. Online retrieval

Input:

- a user query
- a loaded offline index
- optional LLM
- optional embedding model

Execution route:

1. progressive LLM tree search
2. embedding full-top-k backfill
3. BM25 full-top-k backfill
4. ordered dedupe merge

Ownership:

- [retrieval/tree](./retrieval/tree)
- [retrieval/semantic](./retrieval/semantic)
- [retrieval/lexical](./retrieval/lexical)
- [retrieval/merge](./retrieval/merge)
- [retrieval/service](./retrieval/service)

### 3. Orchestration

Input:

- retrieval results
- CID tree runtime
- user conversation state

Responsibilities:

- build runtime state
- route turns
- call retrieval when needed
- dispatch leaf nodes
- return final user-facing results

Ownership:

- `orchestration/engine`
- `orchestration/routing`
- `orchestration/runtime`
- `orchestration/retrieval_adapter`

## Shared Layers

### `models/`

Shared contracts only:

- CID tree objects
- retrieval tree objects
- index record objects
- trace/result objects

### `shared/`

Generic helpers only:

- optional dependency fallbacks such as [shared/rich_compat.py](./shared/rich_compat.py)

## Current Package Roles

- [indexing](./indexing): offline build
- [retrieval](./retrieval): online retrieval
- [orchestration](./orchestration): runtime orchestration
- `demo`: runnable examples that import and use the SDK
- `training`: training/eval built on top of the SDK
- [models](./models): shared contracts
- [shared](./shared): shared utilities
- `data`: local assets and generated artifacts, not imported by core packages
- `tests`: verification only
- `scripts`: local tooling only

## Final State

The refactor is complete:

- no legacy runtime package roots remain
- no compatibility shells remain
- canonical packages own all live implementation code
- documentation describes the current architecture, not a transition state
