# Merge changes from the original project

Rules: `../architecture/decisions/0010-independent-project-own-repository.md` (when and why),
`../architecture/decisions/0002-stay-close-to-upstream.md` (code layout).

The original is not a configured remote — the remote `upstream` is EDMRN's own repository. Fetch
the original by URL:

```
git -C X:/Projects/EDProjects/EDMRN fetch https://github.com/NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer.git main
git -C X:/Projects/EDProjects/EDMRN log --oneline main..FETCH_HEAD
git -C X:/Projects/EDProjects/EDMRN merge FETCH_HEAD
```

Read the log first and merge only when the changes are worth it; a single fix can be taken with
`git cherry-pick` instead. Merge on a branch when our feature work is in flight. Conflicts should
only appear at our hook points in inherited files; a conflict anywhere else means an ADR-0002
violation slipped in. After the merge: run the tests, start the app once, and if `edmrn.spec` or
`requirements.txt` changed, rebuild with PyInstaller and check that `edmrn/ext/` is in the bundle.
