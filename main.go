package main

import (
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"

	router "github.com/v2fly/v2ray-core/v5/app/router/routercommon"
	"google.golang.org/protobuf/proto"
)

var youtubeKW = []string{"youtube", "youtu.be", "ytimg", "googlevideo", "withyoutube"}

var extraDomains = []string{"rulate.ru", "hentailib.me", "ranobelib.me"}

// Additional dlc.dat sections to inject into DOMAINS (by name)
var injectSections = []string{"EHENTAI"}

var dropSections = map[string]bool{
	"BYPASS": true, "CN": true, "RU": true,
}

const (
	sectionDomains = "DOMAINS"
	sectionYouTube = "YOUTUBE"
	sectionIP      = "IP"
)

func isYouTube(d *router.Domain) bool {
	v := strings.ToLower(d.Value)
	for _, kw := range youtubeKW {
		if strings.Contains(v, kw) {
			return true
		}
	}
	return false
}

func main() {
	zkeenURL := flag.String("zkeen-url", "https://github.com/jameszeroX/zkeen-domains/releases/latest/download/zkeen.dat", "zkeen.dat download URL")
	zkeenPath := flag.String("zkeen", "", "Local zkeen.dat (overrides URL)")
	zkeenipURL := flag.String("zkeenip-url", "https://github.com/jameszeroX/zkeen-ip/releases/latest/download/zkeenip.dat", "zkeenip.dat download URL")
	zkeenipPath := flag.String("zkeenip", "", "Local zkeenip.dat (overrides URL)")
	dlcURL := flag.String("dlc-url", "https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat", "dlc.dat download URL")
	dlcPath := flag.String("dlc", "", "Local dlc.dat (overrides URL)")
	ehentaiName := flag.String("inject", "", "Comma-separated dlc.dat section names to inject into DOMAINS (overrides defaults)")
	outputDir := flag.String("out", ".", "Output directory")
	flag.Parse()

	// ── Load sources ──
	fmt.Println("=== Loading sources ===")
	zkeenData := loadFile(*zkeenPath, *zkeenURL, "zkeen.dat")
	zkeenipData := loadFile(*zkeenipPath, *zkeenipURL, "zkeenip.dat")
	dlcData := loadFile(*dlcPath, *dlcURL, "dlc.dat")

	zkeen := parseGeoSite(zkeenData, "zkeen.dat")
	zkeenip := parseGeoIP(zkeenipData, "zkeenip.dat")
	dlc := parseGeoSite(dlcData, "dlc.dat")

	// ── Step 1: zkeen-patched.dat ──
	fmt.Println("\n=== Step 1: zkeen → zkeen-patched ===")
	zkeenPatched := patchZkeen(zkeen)
	writeGeoSite(zkeenPatched, *outputDir+"/zkeen-patched.dat")

	// ── Step 2: geosite-matched.dat (dynamic from zkeenip sections) ──
	fmt.Println("\n=== Step 2: zkeenip sections → geosite-matched ===")
	geositeMatched := buildGeositeMatched(zkeenip, dlc)
	writeGeoSite(geositeMatched, *outputDir+"/geosite-matched.dat")

	// ── Step 3: merge ──
	fmt.Println("\n=== Step 3: merge zkeen-patched + geosite-matched ===")
	merged := mergeGeoSites(zkeenPatched, geositeMatched)

	// ── Step 4: inject extra sections + domains ──
	sections := injectSections
	if *ehentaiName != "" {
		sections = strings.Split(strings.ToUpper(*ehentaiName), ",")
	}
	fmt.Println("\n=== Step 4: inject extra sections + domains ===")
	merged = injectExtra(merged, dlc, sections, extraDomains)
	writeGeoSite(merged, *outputDir+"/merged.dat")

	// ── Step 5: zkeenip-patched.dat ──
	fmt.Println("\n=== Step 5: zkeenip → zkeenip-patched ===")
	zkeenipPatched := patchZkeenip(zkeenip)
	writeGeoIP(zkeenipPatched, *outputDir+"/zkeenip-patched.dat")

	fmt.Println("\n=== Done ===")
}

// ── Load / parse helpers ──

func loadFile(localPath, url, name string) []byte {
	var data []byte
	var err error
	if localPath != "" {
		fmt.Printf("  Reading %s from %s\n", name, localPath)
		data, err = os.ReadFile(localPath)
	} else {
		fmt.Printf("  Downloading %s from %s\n", name, url)
		data, err = download(url)
	}
	check(err, "load "+name)
	fmt.Printf("  %s: %d bytes\n", name, len(data))
	return data
}

func parseGeoSite(data []byte, name string) *router.GeoSiteList {
	list := &router.GeoSiteList{}
	check(proto.Unmarshal(data, list), "parse "+name)
	return list
}

func parseGeoIP(data []byte, name string) *router.GeoIPList {
	list := &router.GeoIPList{}
	check(proto.Unmarshal(data, list), "parse "+name)
	return list
}

func writeGeoSite(list *router.GeoSiteList, path string) {
	out, err := proto.Marshal(list)
	check(err, "marshal "+path)
	check(os.WriteFile(path, out, 0644), "write "+path)
	fmt.Printf("  → %s (%d bytes)\n", path, len(out))
	for _, e := range list.Entry {
		fmt.Printf("    %s (%d)\n", e.CountryCode, len(e.Domain))
	}
}

func writeGeoIP(list *router.GeoIPList, path string) {
	out, err := proto.Marshal(list)
	check(err, "marshal "+path)
	check(os.WriteFile(path, out, 0644), "write "+path)
	fmt.Printf("  → %s (%d bytes)\n", path, len(out))
	for _, e := range list.Entry {
		fmt.Printf("    %s (%d CIDRs)\n", e.CountryCode, len(e.Cidr))
	}
}

// ── Step 1: patch zkeen.dat ──

func patchZkeen(zkeen *router.GeoSiteList) *router.GeoSiteList {
	fmt.Printf("  Input: %d sections\n", len(zkeen.Entry))
	for _, e := range zkeen.Entry {
		fmt.Printf("    %s (%d)\n", e.CountryCode, len(e.Domain))
	}

	var filtered []*router.GeoSite
	for _, e := range zkeen.Entry {
		if !dropSections[strings.ToUpper(e.CountryCode)] {
			filtered = append(filtered, e)
		} else {
			fmt.Printf("    Drop: %s\n", e.CountryCode)
		}
	}

	merged := &router.GeoSite{CountryCode: sectionDomains}
	var rest []*router.GeoSite
	for _, e := range filtered {
		if strings.ToUpper(e.CountryCode) == sectionYouTube {
			rest = append(rest, e)
		} else {
			merged.Domain = append(merged.Domain, e.Domain...)
		}
	}
	merged.Domain = dedupDomains(merged.Domain)

	fmt.Printf("  Merged into DOMAINS: %d domains\n", len(merged.Domain))
	return &router.GeoSiteList{Entry: append([]*router.GeoSite{merged}, rest...)}
}

// ── Step 2: build geosite-matched from zkeenip sections ──

func buildGeositeMatched(zkeenip *router.GeoIPList, dlc *router.GeoSiteList) *router.GeoSiteList {
	sectionNames := map[string]bool{}
	fmt.Println("  zkeenip.dat sections:")
	for _, e := range zkeenip.Entry {
		name := strings.ToUpper(e.CountryCode)
		fmt.Printf("    %s (%d CIDRs)\n", e.CountryCode, len(e.Cidr))
		if !dropSections[name] && name != sectionYouTube {
			sectionNames[name] = true
		}
	}

	// Build dlc index
	dlcIdx := map[string]*router.GeoSite{}
	for _, e := range dlc.Entry {
		dlcIdx[strings.ToUpper(e.CountryCode)] = e
	}

	// Find matching sections in dlc.dat
	fmt.Println("  Matching dlc.dat sections:")
	var allDomains []*router.Domain
	for name := range sectionNames {
		if section, ok := dlcIdx[name]; ok {
			fmt.Printf("    ✓ %s (%d domains)\n", name, len(section.Domain))
			allDomains = append(allDomains, section.Domain...)
		} else {
			fmt.Printf("    ✗ %s — not found in dlc.dat\n", name)
		}
	}
	fmt.Printf("  Total collected: %d domains\n", len(allDomains))

	domains := &router.GeoSite{CountryCode: sectionDomains}
	youtube := &router.GeoSite{CountryCode: sectionYouTube}
	for _, d := range allDomains {
		if isYouTube(d) {
			youtube.Domain = append(youtube.Domain, d)
		} else {
			domains.Domain = append(domains.Domain, d)
		}
	}
	domains.Domain = dedupDomains(domains.Domain)
	youtube.Domain = dedupDomains(youtube.Domain)

	return &router.GeoSiteList{Entry: []*router.GeoSite{domains, youtube}}
}

// ── Step 3: merge two GeoSiteLists ──

func mergeGeoSites(a, b *router.GeoSiteList) *router.GeoSiteList {
	sections := map[string][]*router.Domain{}
	var order []string
	seen := map[string]bool{}

	for _, e := range a.Entry {
		name := strings.ToUpper(e.CountryCode)
		sections[name] = append(sections[name], e.Domain...)
		if !seen[name] {
			order = append(order, name)
			seen[name] = true
		}
	}
	for _, e := range b.Entry {
		name := strings.ToUpper(e.CountryCode)
		sections[name] = append(sections[name], e.Domain...)
		if !seen[name] {
			order = append(order, name)
			seen[name] = true
		}
	}

	var totalBefore, totalAfter int
	var result []*router.GeoSite
	for _, name := range order {
		before := len(sections[name])
		domains := dedupDomains(sections[name])
		totalBefore += before
		totalAfter += len(domains)
		removed := before - len(domains)
		fmt.Printf("    %s: %d → %d (-%d)\n", name, before, len(domains), removed)
		result = append(result, &router.GeoSite{CountryCode: name, Domain: domains})
	}
	fmt.Printf("  Total: %d → %d (-%d duplicates)\n", totalBefore, totalAfter, totalBefore-totalAfter)
	return &router.GeoSiteList{Entry: result}
}

// ── Step 4: inject extra sections + domains ──

func injectExtra(merged *router.GeoSiteList, dlc *router.GeoSiteList, sectionNames []string, extras []string) *router.GeoSiteList {
	var injected []*router.Domain
	dlcIdx := map[string]*router.GeoSite{}
	for _, e := range dlc.Entry {
		dlcIdx[strings.ToUpper(e.CountryCode)] = e
	}
	for _, name := range sectionNames {
		name = strings.TrimSpace(strings.ToUpper(name))
		if s, ok := dlcIdx[name]; ok {
			injected = append(injected, s.Domain...)
			fmt.Printf("  Injected %s (%d domains)\n", name, len(s.Domain))
		} else {
			fmt.Printf("  %s — not found in dlc.dat\n", name)
		}
	}

	for _, d := range extras {
		injected = append(injected, &router.Domain{
			Type:  router.Domain_RootDomain,
			Value: d,
		})
		fmt.Printf("  Added: %s\n", d)
	}

	for _, e := range merged.Entry {
		if strings.ToUpper(e.CountryCode) == sectionDomains {
			before := len(e.Domain)
			e.Domain = append(e.Domain, injected...)
			e.Domain = dedupDomains(e.Domain)
			fmt.Printf("  DOMAINS: %d → %d (-%d duplicates)\n", before, len(e.Domain), before+len(injected)-len(e.Domain))
			break
		}
	}

	fmt.Println("  Cross-section check:")
	hasOverlap := false
	for i := 0; i < len(merged.Entry); i++ {
		for j := i + 1; j < len(merged.Entry); j++ {
			count := overlapCount(merged.Entry[i].Domain, merged.Entry[j].Domain)
			if count > 0 {
				fmt.Printf("    %s ∩ %s = %d overlaps\n", merged.Entry[i].CountryCode, merged.Entry[j].CountryCode, count)
				hasOverlap = true
			}
		}
	}
	if !hasOverlap {
		fmt.Println("    0 overlaps ✓")
	}

	return merged
}

// ── Step 5: patch zkeenip.dat ──

func patchZkeenip(zkeenip *router.GeoIPList) *router.GeoIPList {
	fmt.Printf("  Input: %d sections\n", len(zkeenip.Entry))

	var filtered []*router.GeoIP
	for _, e := range zkeenip.Entry {
		if dropSections[strings.ToUpper(e.CountryCode)] {
			fmt.Printf("    Drop: %s (%d CIDRs)\n", e.CountryCode, len(e.Cidr))
		} else {
			filtered = append(filtered, e)
		}
	}

	ip := &router.GeoIP{CountryCode: sectionIP}
	yt := &router.GeoIP{CountryCode: sectionYouTube}
	for _, e := range filtered {
		if strings.ToUpper(e.CountryCode) == sectionYouTube {
			yt.Cidr = append(yt.Cidr, e.Cidr...)
		} else {
			ip.Cidr = append(ip.Cidr, e.Cidr...)
		}
	}

	ip.Cidr = dedupCIDRs(ip.Cidr)
	yt.Cidr = dedupCIDRs(yt.Cidr)

	fmt.Printf("  IP: %d, YOUTUBE: %d\n", len(ip.Cidr), len(yt.Cidr))
	return &router.GeoIPList{Entry: []*router.GeoIP{ip, yt}}
}

// ── Dedup helpers ──

func domainKey(d *router.Domain) string {
	return strconv.Itoa(int(d.Type)) + ":" + strings.ToLower(d.Value)
}

func dedupDomains(domains []*router.Domain) []*router.Domain {
	seen := map[string]bool{}
	result := make([]*router.Domain, 0, len(domains))
	for _, d := range domains {
		key := domainKey(d)
		if !seen[key] {
			seen[key] = true
			result = append(result, d)
		}
	}
	return result
}

func dedupCIDRs(cidrs []*router.CIDR) []*router.CIDR {
	seen := map[string]bool{}
	result := make([]*router.CIDR, 0, len(cidrs))
	for _, c := range cidrs {
		key := string(c.Ip) + "/" + strconv.Itoa(int(c.Prefix))
		if !seen[key] {
			seen[key] = true
			result = append(result, c)
		}
	}
	return result
}

func overlapCount(a, b []*router.Domain) int {
	set := map[string]bool{}
	for _, d := range a {
		set[domainKey(d)] = true
	}
	count := 0
	for _, d := range b {
		if set[domainKey(d)] {
			count++
		}
	}
	return count
}

// ── Generic helpers ──

func download(url string) ([]byte, error) {
	resp, err := http.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, resp.Status)
	}
	return io.ReadAll(resp.Body)
}

func check(err error, msg string) {
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %s: %v\n", msg, err)
		os.Exit(1)
	}
}
