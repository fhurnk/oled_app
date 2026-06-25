# GitHub And Release Workflow

This project treats every meaningful change as a versioned release.

## Required Steps

1. Choose the next version in the `vMAJOR.MIDDLE.MINOR` format.
2. Update `APP_VERSION` in `oled_measurement_app_v2_5.py`.
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
.\env\Scripts\python.exe .\scripts\create_github_release.py vX.Y.Z --repo fhurnk/oled_app
.\env\Scripts\python.exe .\scripts\create_github_release.py vX.Y.Z --dry-run
```

## Local Git Note

On this machine the repository may be stored in `.git-local`. If normal `git` does not detect the repository, set:

```powershell
$env:GIT_DIR = ".git-local"
$env:GIT_WORK_TREE = (Get-Location).Path
```

Then run the same status, diff, add, commit, push, and tag commands.
