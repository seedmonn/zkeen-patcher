# update_geofiles.py

Updates `ip.dat`/`geo.dat` on all nodes from the latest zkeen-patcher release and reloads xray/xkeen/geo-updater.

## Config: where the creds live

Targets (hosts, tokens, passwords) are **never committed** — only `scripts/targets.example.json` (placeholders). Provide them one of two ways:

### A. GitHub Variable `TARGETS_JSON` (recommended — portable, disaster-recoverable)

The config lives in a repo Variable, private to collaborators (a public repo's Variables are **not** exposed by `git clone` — only the code is).

```bash
# one-time, from a machine that has the real config:
gh variable set TARGETS_JSON --body "$(cat targets.json)"
```

Then clone + run on **any** machine — no local secrets file:
```bash
git clone https://github.com/seedmonn/zkeen-patcher && cd zkeen-patcher
pip install --user -r requirements.txt
TARGETS_JSON="$(gh variable get TARGETS_JSON)" python3 scripts/update_geofiles.py
```
If the machine is lost, the creds are still in GitHub — recover with `gh variable get TARGETS_JSON`.

**Security:** enable **2FA** on your GitHub account (a stolen account can read the Variable). Variables are **not** masked in Actions logs, so never print `TARGETS_JSON` (the script never does); if you ever run this inside Actions, use a (write-only) **Secret** instead.

### B. Local file

```bash
mkdir -p ~/.config/zkeen-patcher
cp scripts/targets.example.json ~/.config/zkeen-patcher/targets.json
chmod 600 ~/.config/zkeen-patcher/targets.json
# edit: fill <TOKEN>, <basePath>, <SUDO_PASSWORD>, <ROUTER_PASSWORD>, hosts
python3 scripts/update_geofiles.py
```
(`TARGETS_JSON` env from mode A takes precedence over `--config`.)

The ed25519 key must be in ssh-agent (`ssh-add -l`) for MSK/SPB/EST/LAN-MIRROR.

## Usage
```bash
python3 scripts/update_geofiles.py --dry-run       # preflight + plan, no changes
python3 scripts/update_geofiles.py --only MSK      # one target
python3 scripts/update_geofiles.py                 # all
```
Exit code 0 iff every target succeeded.
