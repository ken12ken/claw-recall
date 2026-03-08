# Claw Recall — Project Instructions

## Documentation Freshness Rule (MANDATORY)

**Before creating any PR**, check whether your changes affect documentation:

1. If you **renamed, moved, or deleted** any file, module, function, CLI flag, or endpoint:
   - Search `README.md`, `docs/guide.md`, `CONTRIBUTING.md`, and `CHANGELOG.md` for references
   - Search the internal reference doc: `~/clawd/reference/claw-recall-reference.md`
   - Update any stale references in the same PR

2. If you **added** a new feature, endpoint, CLI flag, or MCP tool:
   - Add it to the appropriate section in `README.md` (if user-facing) or `docs/guide.md` (if operational)
   - Update the internal reference doc if it covers that area

3. If you **changed behavior** of an existing feature:
   - Check whether any doc describes the old behavior and update it

**Quick scan command:**
```bash
# After making changes, check what docs reference the files you touched:
git diff --name-only HEAD~1 | xargs -I{} basename {} | xargs -I{} grep -rn {} README.md docs/ CONTRIBUTING.md
```

## Code Standards

- All modules invoked as `python3 -m claw_recall.xxx`, never as script files
- Tests: `python3 -m pytest tests/test_claw_recall.py -v`
- This is a **public repo** — never commit internal IPs, hostnames, paths, API keys, or agent names
- All changes go through PRs — never push directly to master
- Version in `VERSION` file — update when releasing (see Release Procedure below)

## Release Procedure

Claw Recall follows [Semantic Versioning](https://semver.org/):
- **Patch (x.y.Z)** — bug fixes, small improvements. Batch a few PRs together.
- **Minor (x.Y.0)** — new features (MCP tool, CLI flag, source type, notable behavior change).
- **Major (X.0.0)** — breaking changes (API changes, config format changes, dropped support).

**When to release:** After merging sufficient changes, ask Rod: *"N PRs merged since vX.Y.Z — ready to cut vX.Y.Z+1?"* Don't release after every single PR — group related changes.

**Steps:**
1. Create a branch: `git checkout -b release/vX.Y.Z`
2. Update `VERSION` file with the new version
3. Add a new section to `CHANGELOG.md` (date, summary, Changed/Added/Fixed subsections)
4. Update the README badge: `[![Version](https://img.shields.io/badge/version-X.Y.Z-blue)]`
5. Commit, push, create PR, merge
6. Tag and release from master:
   ```bash
   git checkout master && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   gh release create vX.Y.Z -R rodbland2021/claw-recall \
     --title "vX.Y.Z — Summary" --notes-file /tmp/release-notes.md
   ```
7. Verify: `gh release list -R rodbland2021/claw-recall -L 1`

## Package Layout

```
claw_recall/           # All source code
  config.py            # Settings (DB_PATH, embedding config, etc.)
  database.py          # Connection manager
  cli.py               # CLI entry point
  search/engine.py     # Search engine
  search/files.py      # File search
  capture/thoughts.py  # Thought capture
  capture/sources.py   # Gmail/Drive/Slack
  indexing/indexer.py   # Session indexer
  indexing/watcher.py   # File watcher
  api/web.py           # Flask REST API
  api/mcp_stdio.py     # MCP stdio server
  api/mcp_sse.py       # MCP SSE server
```
