# update_geofiles.py

Updates `ip.dat`/`geo.dat` on all nodes from the latest zkeen-patcher release and reloads xray/xkeen/geo-updater.

## Setup (one-time)
```bash
pip install --user -r requirements.txt
mkdir -p ~/.config/zkeen-patcher
cp scripts/targets.example.json ~/.config/zkeen-patcher/targets.json
chmod 600 ~/.config/zkeen-patcher/targets.json
# edit targets.json: fill <TOKEN_*>, <basePath_*>, <SUDO_PASSWORD>, <ROUTER_PASSWORD>
```
The ed25519 key must be loaded in ssh-agent (`ssh-add -l`) for MSK/SPB/EST/LAN-MIRROR.

## Usage
```bash
python3 scripts/update_geofiles.py --dry-run       # preflight + plan
python3 scripts/update_geofiles.py --only MSK -v   # one target
python3 scripts/update_geofiles.py                 # all
```
Exit code 0 iff every target succeeded.
