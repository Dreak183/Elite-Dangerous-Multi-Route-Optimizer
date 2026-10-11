# ADR-0001: Journal data comes from upstream's journal layer, not EDJournalData

Status: Accepted
Date: 2026-10-11

## Context

The EDProjects hub provides EDJournalData, a local journal data service, and other hub projects
consume it instead of parsing journals. EDMRN is different on two counts: upstream already reads the
journal (`edmrn/journal.py`, `journal_handler.py`, `journal_cache.py`), and the tool is meant to be
run by friends of the commander who will not install or operate a separate local service.

## Decision

Every feature that needs journal or `Status.json` data — current location first — builds on and
extends upstream's journal layer. EDJournalData is neither required nor an optional second source.

## Consequences

EDMRN stays a single self-contained desktop application that a friend can install from one release.
Journal parsing quirks documented in `../../../../knowledge/reference-data.md` must be handled in
this codebase; fixes found here are offered upstream where they touch upstream files. Adding
EDJournalData later would create a second code path for the same data and needs a superseding ADR.
