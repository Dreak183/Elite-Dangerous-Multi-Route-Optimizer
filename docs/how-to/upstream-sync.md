# Merge upstream changes

Rule: `../architecture/decisions/0002-stay-close-to-upstream.md`.

```
git -C X:/Projects/EDProjects/EDMRN fetch upstream
git -C X:/Projects/EDProjects/EDMRN log --oneline main..upstream/main
git -C X:/Projects/EDProjects/EDMRN merge upstream/main
```

Merge on a branch when our feature work is in flight. Conflicts should only appear at our hook
points in upstream files; a conflict anywhere else means an ADR-0002 violation slipped in. After the
merge: run the tests, start the app once, and if `edmrn.spec` or `requirements.txt` changed, rebuild
with PyInstaller and check that `edmrn/ext/` is in the bundle.
