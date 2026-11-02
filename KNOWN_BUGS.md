# Known Bugs

Open issues found by the 2026-09-01 full-repo review (multi-agent code review,
each finding adversarially verified against the source). All of them predate
the REDDIT-section work — the review branch itself came back clean after fixes.

Severity order: the first entry is operational risk worth fixing soon; the rest
are robustness/hygiene issues that bite in specific situations.

## 1. Rollback can silently no-op while the script reports "rolled back" — important

- **Where:** `scripts/update_geofiles.py:282` (`_restore` discards the SSH exit
  status), `scripts/update_geofiles.py:115` (`build_restore_command` ends with
  `|| true`); misleading reports at `apply_xui` (lines 348–352, 357–363) and
  `apply_router` (lines 402–405).
- **What:** every rollback path funnels through `_restore()`, which ignores the
  command's rc, and the inner restore script's `|| true` forces rc 0 — so a
  restore that did nothing (missing `.bak`, failed `mv`, sudo auth change)
  still looks successful. No code path re-verifies the target file's SHA after
  a restore.
- **Failure scenario:** first-ever apply to a node — the live file doesn't
  exist yet, so no `.bak` is created. The apply succeeds, xray restarts and
  dies on the new file, rollback runs `[ -f x.bak ] && mv … || true` (a no-op),
  and the summary still says "rolled back". The node stays down while the
  operator believes it was restored.
- **Fix direction:** make `_restore()` return/inspect rc, drop the `|| true`,
  re-fetch the target SHA after restoring and compare it to the pre-apply SHA;
  report "restore failed / no backup existed" as a distinct outcome.

## 2. `ssh_exec` can hang forever — minor [FIXED 2026-11-02]

- **Where:** `scripts/update_geofiles.py:228`.
- **Status:** Fixed by adding channel wall-clock polling deadline before `recv_exit_status()`.

## 3. `apply_mirror` restarts the container even when the mirror is current — minor [FIXED 2026-09-16 / tested 2026-11-02]

- **Where:** `scripts/update_geofiles.py:429`.
- **Status:** Fixed in commit `75226e9` by checking `wait_mirror(..., 5)` before restart; unit tests updated in `test_flows.py`.

## 4. Config validation covers ALL targets before `--only` filtering — minor [FIXED 2026-11-02]

- **Where:** `scripts/update_geofiles.py:474`.
- **Status:** Fixed by running `filter_targets()` before target validation.

## 5. `download()` has no timeout — minor

- **Where:** `main.go:516` (`http.Get`, the default client — no deadline, no
  retry); on the path of all three source downloads.
- **Failure scenario:** GitHub's CDN accepts the connection but stalls
  mid-body during the daily 03:03 build → `io.ReadAll` blocks until the
  Actions job timeout (default 360 min) kills the job; the day's release is
  never published and nodes silently keep stale geo files.
- **Fix direction:** use a shared `http.Client{Timeout: …}` (e.g. 120 s);
  optionally add one retry.

## 6. SSH host keys are auto-accepted — minor

- **Where:** `scripts/update_geofiles.py:209` (`AutoAddPolicy`, no known_hosts
  pinning anywhere).
- **What:** every connection (root SSH to the VPS targets, sudo password on
  stdin, router password) accepts whatever host key is presented.
- **Failure scenario:** run from a laptop on hostile Wi-Fi — an on-path
  attacker presents their own host key, terminates the SSH session, and
  captures the sudo password / panel token, or substitutes ip.dat/geo.dat so
  XRay routes through attacker-chosen ranges. Nothing detects the key change.
- **Fix direction:** pin host keys (per-target `known_hosts` entry or system
  `load_host_keys` + `RejectPolicy`), with a documented first-connect
  bootstrap step.

---

Fixed-along-the-way note: the review also flagged four issues in the REDDIT
work itself (missing-source logging, two test gaps, stdout-capture hygiene) —
all were fixed in commit `24abb50` before merge.
