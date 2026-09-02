# Development

Install `requirements-dev.txt`, then run `ruff check backend tests` and `pytest -q`. Keep event mutations behind the ledger, preserve `/api/v1` compatibility, and treat `/api/v2` as the runtime contract. Benchmark outputs must retain the `runtime_simulation` label. Godot changes require static contracts plus an engine-level smoke run when the executable is available; distinguish headless transport coverage from graphical UI review.

## Offline Docker runtime validation

Use `scripts/validate_docker_runtime.ps1` only with an explicitly supplied,
non-dot defaults file, a unique Compose project name, a fresh evidence root, a
known local image, and a unique loopback port. The validator passes
`--env-file` on every Compose invocation, uses `--no-build --pull never`, and
never reads `.env*`, builds, pulls, or removes volumes. It records configuration,
health, restart, SQLite, and log evidence with create-only writes, then runs
`compose down --remove-orphans` without `-v`.

Example (replace the image and port with values already present locally):

```powershell
.\scripts\validate_docker_runtime.ps1 `
  -DefaultsFile .\config\compose-validation.defaults `
  -EvidenceRoot D:\ProjectTown-usability\docker-runtime-20260831-001 `
  -ProjectName projecttown-validation-20260831-001 `
  -Image projecttown:local-validation `
  -HostPort 18080
```

The validator does not install dependencies. `requirements-dev.txt` now declares
`httpx2>=2,<3`, required by the installed Starlette TestClient adapter, but the
existing TestClient deprecation warning remains expected until a separately
authorized dependency installation and full regression run confirm the upgrade.

`pytest -ra` reports the reason for each skip. Live-provider tests remain an
intentional offline gate; Windows-unavailable symlink, hard-link, FIFO, and
container-lock tests require their documented Linux/Docker environment and must
not be relabelled as passing locally.
