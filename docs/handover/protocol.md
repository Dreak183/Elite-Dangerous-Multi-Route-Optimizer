# Handover protocol

Why this exists: the global working rules. A long session carries its whole history
into every later turn, so the cost of continuing rises while the quality of attention falls. A handover ends that curve. The rule
is to hand over *early* — at the last point where the session is still cheap — not when it is
already exhausted.

## When to write one

Write a handover **before** any of these, not after:

- A phase or milestone boundary.
- Any point where the next step needs the human's decision, test result, or approval. Waiting for a
  human is the most expensive possible use of an accumulated session.
- Any point where the session has become slow, repetitive, or is about to be compacted.
- Before switching to a substantially different kind of work.

## What a handover must survive

The next session starts with **zero** memory of this one. It gets: the repository, `CLAUDE.md`, and
`HANDOVER.md`. Nothing else. Write for that reader.

If continuing requires something that lives only in this session — a conclusion, a rejected
approach, a rule you derived — that thing does not belong in the handover. It belongs in
`architecture/`, an ADR, or `knowledge/`, and the handover links to it. The handover is a pointer
document, not a knowledge store.

## The procedure

1. **Sweep the documentation.** Re-read `../../CLAUDE.md`, `../architecture/`, `../how-to/` and
   `../knowledge/`. Correct every statement this stretch of work falsified. Delete what is
   superseded. This is the step that gets skipped; it is the step that matters.
2. **Promote durable conclusions** out of scratch (the execution ledger, the transcript, chat
   messages) into their permanent home. Anything not promoted is lost — treat the ledger as
   deletable, because it is.
3. **Verify the facts you are about to assert.** Run the tests, run the linter, check
   `git status` and `git log`. Never write a state you did not just observe.
4. **Rewrite `HANDOVER.md`** — replace it entirely, do not append. Keep it under one screen.
5. **Commit** the documentation and the handover together with explicit paths.
6. **Tell the human** what is done, what needs their input, and that a fresh session can start from
   `docs/handover/HANDOVER.md`.

## The shape of HANDOVER.md

Five sections, in this order, nothing else:

- **Where things stand** — branch, HEAD, test and lint state, what is built and runnable. Facts you
  just verified.
- **Start here** — the exact first actions for a fresh session, in order, with paths.
- **Open decisions** — what needs the human, and what is blocked until they answer.
- **Traps** — the specific ways this project has already gone wrong, and the ones it is about to.
  Not general advice; only what would cost the next session real time.
- **Where the knowledge lives** — pointers to the permanent documents, and which of them is
  authoritative when they disagree.
