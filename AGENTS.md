# Codex Project Instructions

## Read First

When a new Codex chat starts in this repository, read these files before changing code:

1. `CODEX_PROJECT.md` - concise machine-oriented project map and current-version pointers.
2. `docs/project_manifest.json` - structured source of truth for paths, version files, release commands, and ignored local data.
3. `AGENTS.md` - operational rules for Codex work.
4. `CHANGELOG.md` and the latest `docs/versions/vX.Y.Z.md` - recent behavior changes.

## Versioning, Changelog, And GitHub Releases

During the current prerelease modularization work, keep publishing migration progress to the existing `v1.7.2` release/tag instead of creating a new version for every internal migration step. Update `APP_VERSION`, `CHANGELOG.md`, `docs/versions/v1.7.2.md`, visible docs, the `v1.7.2` tag, and the GitHub Release body together. Do not create later modularization releases unless the user explicitly asks for a new project version.

For every user-visible behavior change, bug fix, documentation change, or release preparation:

1. Update `APP_VERSION` in `oled_app/constants.py`.
2. Use semantic project versions in the existing format `vMAJOR.MIDDLE.MINOR`.
   - Increment `MINOR` for fixes and small improvements.
   - Increment `MIDDLE` for feature additions or workflow changes.
   - Increment `MAJOR` only for breaking data/layout/project changes.
3. Add a new top entry to `CHANGELOG.md` with the date and concise sections such as `Добавлено`, `Изменено`, `Исправлено`.
4. Add `docs/versions/vX.Y.Z.md` for the new version.
5. Update visible current-version references in `README.md`, `PROJECT_OVERVIEW.md`, `CODEX_PROJECT.md`, and `docs/project_manifest.json`.
6. If `docs/versions/` is listed in any project tree, include the new version file there.
7. Commit the version bump and code/docs changes together with a short message.
8. Publish the commit to GitHub.
9. Create a git tag for the exact version, for example `v1.5.4`, and push it.
10. Create a GitHub Release for every version tag using `scripts/create_github_release.py vX.Y.Z`, or `gh release create` if GitHub CLI is available. The release title should be the version, and the body should summarize the matching `CHANGELOG.md` entry and link or mention `docs/versions/vX.Y.Z.md`.

Do not leave a code change without a matching version/changelog entry unless the user explicitly asks for an unversioned scratch change.

## Repository Hygiene

- Keep generated measurement data out of git. `OLED_series/`, local settings, virtual environments, caches, backups, and IDE artifacts are not release content.
- During modularization, keep `oled_measurement_app_v2_5.py` as the untouched reference app unless the user explicitly asks to change the reference. Put new modular entrypoint work in `oled_modular_app.py` and `oled_app/`.
- Before committing, check the intended diff and stage only files related to the requested change.
- Prefer direct local Git commands when available. This repo may use `.git-local` as the git directory; if normal `git` cannot find the repo, run with `GIT_DIR=.git-local` and `GIT_WORK_TREE` set to the workspace path.
- If publishing requires credentials, use the configured Git Credential Manager or ask the user to authenticate rather than embedding tokens in files or commands.

## Release Checklist

- `APP_VERSION` in `oled_app/constants.py` matches the release version.
- `CHANGELOG.md` has a top entry for the release.
- `docs/versions/vX.Y.Z.md` exists.
- `README.md` and `PROJECT_OVERVIEW.md` show the current version.
- Syntax checks or focused behavior checks have passed.
- Commit is pushed.
- Tag is pushed.
- GitHub Release exists for the tag.

## GitHub Authentication For Future Agents

Перед публикацией сначала проверьте авторизацию GitHub CLI:

```powershell
gh auth status
```

Если `gh` не установлен, попросите пользователя выполнить в обычном PowerShell вне Codex:

```powershell
winget install --id Git.Git
winget install --id GitHub.cli
```

Первоначальную авторизацию также следует выполнять в обычном PowerShell вне Codex, чтобы GitHub CLI мог сохранить сессию в `%APPDATA%`:

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status
gh repo view fhurnk/oled_app
```

Пользователь вводит показанный одноразовый код на странице `https://github.com/login/device`. Не просите пользователя передавать токен в чат и не сохраняйте токены в репозитории.

Если GitHub CLI сообщает `Access is denied` при записи конфигурации, попросите пользователя выполнить в обычном PowerShell:

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\GitHub CLI"
gh auth login --hostname github.com --git-protocol https --web
```

Если появляется падение `git-remote-https.exe` с сообщением `Память не может быть read`, не продолжайте использовать Git из комплекта Visual Studio. Проверьте активный Git:

```powershell
where.exe git
```

Предпочтительный путь после установки Git for Windows:

```text
C:\Program Files\Git\cmd\git.exe
```

После установки или изменения авторизации нужно открыть новое окно PowerShell. Для этого репозитория также учитывайте `.git-local`:

```powershell
$env:GIT_DIR = "$PWD\.git-local"
$env:GIT_WORK_TREE = $PWD
git status -sb
```

Если среда Codex не разрешает запись в `%APPDATA%`, не запускайте повторяющиеся device-login циклы. Остановитесь и попросите пользователя один раз выполнить постоянную авторизацию в обычном PowerShell. Временный `GH_CONFIG_DIR` допустим только как крайний вариант для текущей публикации; после завершения обязательно выполните `gh auth logout` для этого временного каталога и не оставляйте `hosts.yml` с токеном на диске.
