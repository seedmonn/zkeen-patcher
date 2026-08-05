# zkeen-patcher

Combines domain routing lists from [zkeen-domains](https://github.com/jameszeroX/zkeen-domains) and [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community) into custom `.dat` files for XRay/V2Ray routing.

## Download

| File | Description | Sections |
|---|---|---|
| [geosite.dat](https://github.com/seedmonn/zkeen-patcher/releases/latest/download/geosite.dat) | Domains | DOMAINS, YOUTUBE, GEMINI |
| [geoip.dat](https://github.com/seedmonn/zkeen-patcher/releases/latest/download/geoip.dat) | IP ranges (CIDR) | IP, YOUTUBE |

Built daily from latest upstream sources.

## What it does

### geosite.dat
1. Downloads `zkeen.dat` — removes BYPASS, CN, RU; merges DOMAINS + OTHER + POLITIC into one section
2. Reads all section names from `zkeenip.dat` — finds matching domain sections in `dlc.dat` (v2fly), extracts and deduplicates
3. Merges both sources, separates YouTube domains into YOUTUBE section
4. Injects additional dlc.dat sections + custom domains
5. Carves out a separate `GEMINI` section from a dlc.dat category (default `google-deepmind`)

### geoip.dat
1. Downloads `zkeenip.dat` — removes CN, RU
2. Separates YouTube CIDRs into YOUTUBE, rest into IP
3. Deduplicates all CIDRs

## Auto-update geo files on nodes

`scripts/update_geofiles.py` pushes the latest `geoip.dat`/`geosite.dat` to all
nodes (3× 3x-ui VPS, router, LAN geo-updater mirror) and reloads them, verified
by SHA256. See `scripts/README.md`. Secrets live in
`~/.config/zkeen-patcher/targets.json` (never committed).

## Build locally

```bash
git clone https://github.com/seedmonn/zkeen-patcher.git
cd zkeen-patcher
go run . -out .
```

### CLI flags

| Flag | Default | Description |
|---|---|---|
| `-zkeen` | (download) | Local zkeen.dat path |
| `-zkeenip` | (download) | Local zkeenip.dat path |
| `-dlc` | (download) | Local dlc.dat path |
| `-out` | `.` | Output directory |
| `-inject` | (built-in) | Comma-separated dlc.dat sections to inject into DOMAINS |
| `-gemini` | (built-in) | Comma-separated dlc.dat sections to carve into `GEMINI` |
| `-extra-ips` | (empty) | Comma-separated bare IPs/CIDRs appended to the IP section (personal infra — keep in a secret) |
| `-extra-domains` | (empty) | Comma-separated extra domains injected into DOMAINS (personal — keep in a secret) |

Use local files to avoid downloading:
```bash
go run . -zkeen zkeen.dat -zkeenip zkeenip.dat -dlc dlc.dat -out .
```

### Personal IPs/domains (secrets, not in source)

No real infra data lives in the repo. The CI build (`.github/workflows/build.yml`) reads two
**GitHub Secrets** and passes them to the flags above:

```bash
gh secret set EXTRA_IPS     -b "1.2.3.4,5.6.7.8"     -R seedmonn/zkeen-patcher
gh secret set EXTRA_DOMAINS -b "example.com"          -R seedmonn/zkeen-patcher
```

Locally, pass the same via flags: `go run . -out . -extra-ips "…" -extra-domains "…"`.

