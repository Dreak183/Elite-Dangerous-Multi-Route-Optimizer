# ADR-0010: EDMRN develops independently; the original project is an optional source, not a PR target

Status: Accepted
Date: 2026-11-10

Supersedes the remote layout, the regular merge and the PR offer of
[ADR-0002](0002-stay-close-to-upstream.md). ADR-0002's code layout rule stays in force.

## Context

ADR-0002 treated EDMRN as a fork that follows NinurtaKalhu's repository: remote `upstream` = the
original, regular merges of its `main`, useful changes offered back as PRs. The commander ruled on
2026-11-10 that EDMRN is a project that develops on its own. Improvements of the original are merged
only if they are worth it, and no PR to the original is planned. The repository's single remote,
named `upstream`, points at the commander's own GitHub repository
(`Dreak183/Elite-Dangerous-Multi-Route-Optimizer`), and that is intended.

## Decision

- The git remote `upstream` is the commander's repository: EDMRN's own home and its only push
  target. It is not renamed, and no remote for the original is kept.
- In prose, "upstream" keeps meaning the original project and the code inherited from it.
- Changes of the original are taken case by case, fetched by URL
  (`../../how-to/upstream-sync.md`). There is no merge schedule.
- No change is prepared for, or offered to, the original as a PR.
- ADR-0002's layout rule holds unchanged: own features live in `edmrn/ext/`, inherited files get
  only small hook points, dependencies of ours go in `requirements-edmrn.txt`.

## Consequences

A merge of the original stays possible because the layout rule keeps the conflict surface at the
hook points; reversing that rule (refactoring inherited modules in place) still needs its own ADR
and ends cheap merges for good. Without regular merges the distance to the original grows, so each
later merge costs more and fixes of the original arrive late or never. The name `upstream` for the
own repository contradicts git convention: `upstream/main` is EDMRN's own published state, not the
original's — a command copied from a generic fork guide (`git merge upstream/main`) merges nothing
new. Hook points are no longer written with an eventual PR in mind.
