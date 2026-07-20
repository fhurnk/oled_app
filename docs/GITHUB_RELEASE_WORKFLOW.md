# GitHub And Release Workflow

This project treats every meaningful change as a versioned release.

## Required Steps

1. Choose the next version in the `vMAJOR.MIDDLE.MINOR` format.
2. Update `APP_VERSION` in `oled_app/constants.py`.
3. Add a top entry to `CHANGELOG.md`.
4. Add a version note at `docs/versions/vX.Y.Z.md`.
5. Update `README.md` and `PROJECT_OVERVIEW.md`.
6. Run focused checks.
7. Commit the related files together.
8. Push the commit.
9. Create and push a tag:

```powershell
git tag vX.Y.Z
git push <remote> vX.Y.Z
```

10. Create a GitHub Release for the same tag:

```powershell
.\env\Scripts\python.exe .\scripts\create_github_release.py vX.Y.Z
```

The script uses GitHub CLI if `gh` is installed. Otherwise it calls the GitHub REST API and requires `GITHUB_TOKEN` or `GH_TOKEN` in the environment:

```powershell
$env:GITHUB_TOKEN = "<token with repo contents access>"
.\env\Scripts\python.exe .\scripts\create_github_release.py vX.Y.Z
```

Useful options:

```powershell
.\env\Scripts\python.exe .\scripts\create_github_release.py vX.Y.Z --draft
.\env\Scripts\python.exe .\scripts\create_github_release.py vX.Y.Z-alpha --prerelease --latest false
.\env\Scripts\python.exe .\scripts\create_github_release.py vX.Y.Z --repo fhurnk/oled_app
.\env\Scripts\python.exe .\scripts\create_github_release.py vX.Y.Z --dry-run
```

## Codex Execution Environment

Run all network publication operations outside the restricted Codex sandbox. This includes:

- `gh auth status`;
- pushing branches and tags;
- creating or editing a GitHub Release with `gh` or `scripts/create_github_release.py`.

When publishing from Codex, request unsandboxed/escalated command execution so GitHub CLI can read the real `%APPDATA%`, Windows Credential Manager, and system keyring. If this execution mode is unavailable, run the publication commands in a normal PowerShell window.

Do not treat an authentication error seen only inside the sandbox as proof that the user is logged out. Repeat `gh auth status` outside the sandbox before requesting a new login. Local checks, diffs, commits, and tag preparation may remain inside the restricted environment.

## Local Git Note

On this machine the repository may be stored in `.git-local`. If normal `git` does not detect the repository, set:

```powershell
$env:GIT_DIR = ".git-local"
$env:GIT_WORK_TREE = (Get-Location).Path
```

Then run the same status, diff, add, commit, push, and tag commands.
