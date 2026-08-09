# Ranking Storage Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document the current SQLite storage structure and write flow of `ranking_collector` in Chinese.

**Architecture:** Derive every table, field, constraint, index, relationship, and persistence rule from `ranking_collector/repository.py`. Keep runtime-only metric changes separate from persisted records.

**Tech Stack:** Markdown, Mermaid, SQLite

## Global Constraints

- Describe only the current implementation.
- Create the document inside `ranking_collector/`.
- Do not modify application code.
- State that all persisted datetimes are UTC ISO 8601 text.

---

### Task 1: Document the ranking database

**Files:**
- Create: `ranking_collector/数据存储结构.md`

**Interfaces:**
- Consumes: `repository.py`, `models.py`, and the `collect_once()` persistence flow.
- Produces: A maintainers' reference for database structure, constraints, writes, and common queries.

- [ ] **Step 1: Write the schema reference**

Document the database location, entity relationship diagram, three tables, fields, constraints, and indexes.

- [ ] **Step 2: Document persistence behavior**

Explain the run → snapshot → items transaction flow, partial failures, immutable historical snapshots, and cascade deletion.

- [ ] **Step 3: Add examples and query recipes**

Add a representative stored hierarchy and SQL examples for the latest snapshot and a video's history.

- [ ] **Step 4: Verify against source**

Compare every documented identifier against `CREATE_TABLES_SQL` and `save_snapshot()`.

- [ ] **Step 5: Verify patch quality**

Run `git diff --check` and inspect the Markdown diff.
