# Codex Project Instructions

## Versioning, Changelog, And GitHub Releases

For every user-visible behavior change, bug fix, documentation change, or release preparation:

1. Update `APP_VERSION` in `oled_measurement_app_v2_5.py`.
2. Use semantic project versions in the existing format `vMAJOR.MIDDLE.MINOR`.
   - Increment `MINOR` for fixes and small improvements.
   - Increment `MIDDLE` for feature additions or workflow changes.
   - Increment `MAJOR` only for breaking data/layout/project changes.
3. Add a new top entry to `CHANGELOG.md` with the date and concise sections such as `Добавлено`, `Изменено`, `Исправлено`.
4. Add `docs/versions/vX.Y.Z.md` for the new version.
5. Update visible current-version references in `README.md` and `PROJECT_OVERVIEW.md`.
6. If `docs/versions/` is listed in any project tree, include the new version file there.
7. Commit the version bump and code/docs changes together with a short message.
8. Publish the commit to GitHub.
9. Create a git tag for the exact version, for example `v1.5.4`, and push it.
10. Create a GitHub Release for every version tag using `scripts/create_github_release.py vX.Y.Z`, or `gh release create` if GitHub CLI is available. The release title should be the version, and the body should summarize the matching `CHANGELOG.md` entry and link or mention `docs/versions/vX.Y.Z.md`.

Do not leave a code change without a matching version/changelog entry unless the user explicitly asks for an unversioned scratch change.

## Repository Hygiene

- Keep generated measurement data out of git. `OLED_series/`, local settings, virtual environments, caches, backups, and IDE artifacts are not release content.
- Before committing, check the intended diff and stage only files related to the requested change.
- Prefer direct local Git commands when available. This repo may use `.git-local` as the git directory; if normal `git` cannot find the repo, run with `GIT_DIR=.git-local` and `GIT_WORK_TREE` set to the workspace path.
- If publishing requires credentials, use the configured Git Credential Manager or ask the user to authenticate rather than embedding tokens in files or commands.

## Release Checklist

- `APP_VERSION` matches the release version.
- `CHANGELOG.md` has a top entry for the release.
- `docs/versions/vX.Y.Z.md` exists.
- `README.md` and `PROJECT_OVERVIEW.md` show the current version.
- Syntax checks or focused behavior checks have passed.
- Commit is pushed.
- Tag is pushed.
- GitHub Release exists for the tag.
