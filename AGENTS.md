# AGENTS.md

## Cursor Cloud specific instructions

`zkeen-patcher` is a batch/CLI pipeline, not a long-running service. There is **no server, database, or daemon** to keep alive. "Running the app" means executing the Go builder once; it downloads upstream routing lists, transforms them, writes `.dat` files, and exits.

### Components

- **Go builder** (`main.go`, Go 1.24) — the core product. Produces `zkeen-patched.dat`, `geosite-matched.dat`, `merged.dat` (a.k.a. `geosite.dat`), and `zkeenip-patched.dat` (a.k.a. `geoip.dat`).
- **Python deploy script** (`scripts/update_geofiles.py`, Python 3) — optional operational tool that SSHes into remote nodes to push/reload the built files. Needs real target creds (`~/.config/zkeen-patcher/targets.json` or `TARGETS_JSON`) plus an ssh-agent key, so it cannot be exercised end-to-end here; `--help` and the mocked test suite are the safe local checks.

### Build / run / lint / test

- Build: `go build ./...` (or `go run . -out <dir>`).
- Lint/vet: `go vet ./...`.
- Run end-to-end: `go run . -out <dir>` — this downloads `zkeen.dat`, `zkeenip.dat`, and `dlc.dat` over HTTPS from GitHub release assets, so it **requires outbound network egress**. To run fully offline, pass local inputs: `go run . -zkeen zkeen.dat -zkeenip zkeenip.dat -dlc dlc.dat -out <dir>`.
- Tests (Python, fully mocked — no network/SSH/nodes needed): run with `python3 -m pytest`. `conftest.py` puts `scripts/` on `sys.path`.

### Non-obvious gotchas

- `pytest` is installed into `~/.local/bin`, which is **not** on `PATH`. Invoke it as `python3 -m pytest` (not bare `pytest`).
- `pytest` is required by the test suite but is intentionally **not** listed in `requirements.txt`; the update script installs it separately.
- Output `.dat` files are git-ignored (`*.dat` in `.gitignore`). Prefer writing builder output to a scratch dir (e.g. `-out /tmp/...`) to keep the working tree clean.
- The release/CI step (`.github/workflows/build.yml`) renames `merged.dat`→`geosite.dat` and `zkeenip-patched.dat`→`geoip.dat`; the builder itself writes the former names.
