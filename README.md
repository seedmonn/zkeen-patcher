# zkeen-patcher

Combines domain routing lists from [zkeen-domains](https://github.com/jameszeroX/zkeen-domains) and [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community) into custom `.dat` files for XRay/V2Ray routing.

## Download

| File | Description | Sections |
|---|---|---|
| [geosite.dat](https://github.com/seedmonn/zkeen-patcher/releases/latest/download/geosite.dat) | Domains | DOMAINS, YOUTUBE |
| [geoip.dat](https://github.com/seedmonn/zkeen-patcher/releases/latest/download/geoip.dat) | IP ranges (CIDR) | IP, YOUTUBE |

Built daily from latest upstream sources.

## What it does

### geosite.dat
1. Downloads `zkeen.dat` — removes BYPASS, CN, RU; merges DOMAINS + OTHER + POLITIC into one section
2. Reads all section names from `zkeenip.dat` — finds matching domain sections in `dlc.dat` (v2fly), extracts and deduplicates
3. Merges both sources, separates YouTube domains into YOUTUBE section
4. Injects additional dlc.dat sections + custom domains

### geoip.dat
1. Downloads `zkeenip.dat` — removes CN, RU
2. Separates YouTube CIDRs into YOUTUBE, rest into IP
3. Deduplicates all CIDRs

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

Use local files to avoid downloading:
```bash
go run . -zkeen zkeen.dat -zkeenip zkeenip.dat -dlc dlc.dat -out .
```
