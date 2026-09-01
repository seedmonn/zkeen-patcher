package main

import (
	"bytes"
	"io"
	"os"
	"reflect"
	"testing"

	router "github.com/v2fly/v2ray-core/v5/app/router/routercommon"
)

func TestParseExtraIP_IPv4(t *testing.T) {
	c, err := parseExtraIP("1.2.3.4")
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if got := len(c.Ip); got != 4 {
		t.Fatalf("Ip len=%d, want 4", got)
	}
	if c.Prefix != 32 {
		t.Fatalf("Prefix=%d, want 32", c.Prefix)
	}
	if c.Ip[0] != 1 || c.Ip[1] != 2 || c.Ip[2] != 3 || c.Ip[3] != 4 {
		t.Fatalf("Ip=%v, want 1.2.3.4", c.Ip)
	}
}

func TestParseExtraIP_IPv4CIDR(t *testing.T) {
	c, err := parseExtraIP("10.0.0.0/8")
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if len(c.Ip) != 4 || c.Prefix != 8 {
		t.Fatalf("got Ip=%v Prefix=%d", c.Ip, c.Prefix)
	}
}

func TestParseExtraIP_RejectsIPv6(t *testing.T) {
	for _, s := range []string{"2001:db8::1", "2001:db8::/32", "::1"} {
		c, err := parseExtraIP(s)
		if err == nil {
			t.Fatalf("%q: expected error, got CIDR Ip=%v Prefix=%d", s, c.Ip, c.Prefix)
		}
		if c != nil {
			t.Fatalf("%q: expected nil CIDR on error", s)
		}
	}
}

func TestParseExtraIP_RejectsInvalid(t *testing.T) {
	for _, s := range []string{"", "not-an-ip", "999.0.0.1", "1.2.3.0/99"} {
		if _, err := parseExtraIP(s); err == nil {
			t.Fatalf("%q: expected error", s)
		}
	}
}

func TestParseExtraIP_IPv4MappedAccepted(t *testing.T) {
	// Go ParseIP maps ::ffff:a.b.c.d to IPv4; To4 succeeds — accept as IPv4.
	c, err := parseExtraIP("::ffff:8.8.8.8")
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if len(c.Ip) != 4 || c.Prefix != 32 {
		t.Fatalf("got Ip=%v Prefix=%d", c.Ip, c.Prefix)
	}
}

// captureStdout runs fn with os.Stdout redirected into a pipe and returns what
// it printed — copyDlcSections logs its progress to stdout, and tests capture
// (and discard) it to keep `go test` output clean. The deferred restore also
// runs when fn fails the test via t.Fatalf (runtime.Goexit).
func captureStdout(t *testing.T, fn func()) string {
	t.Helper()
	orig := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe: %v", err)
	}
	// Release both pipe FDs and restore stdout on every exit path — return,
	// panic, and Goexit past the explicit Close below. A deferred Close of an
	// already-closed FD only returns an error, which is harmless here.
	defer r.Close()
	defer w.Close()
	defer func() { os.Stdout = orig }()
	os.Stdout = w

	// Drain while fn runs: an undrained pipe buffers only ~16KiB, so a chatty
	// fn would block inside its own Printf long before ReadAll could run.
	var captured bytes.Buffer
	drained := make(chan struct{})
	go func() {
		defer close(drained)
		_, _ = io.Copy(&captured, r) // read error only on Close races; keep what we got
	}()

	fn()
	if err := w.Close(); err != nil {
		t.Fatalf("close pipe writer: %v", err)
	}
	<-drained
	return captured.String()
}

func TestCopyDlcSections_VerbatimInTableOrder(t *testing.T) {
	dlc := &router.GeoSiteList{Entry: []*router.GeoSite{
		{CountryCode: "GOOGLE-DEEPMIND", Domain: []*router.Domain{
			{Type: router.Domain_Plain, Value: "deepmind.google"},
			{Type: router.Domain_RootDomain, Value: "generativelanguage.googleapis.com"},
		}},
		{CountryCode: "Reddit", Domain: []*router.Domain{ // lowercase name must still match
			{Type: router.Domain_RootDomain, Value: "reddit.com"},
			{Type: router.Domain_RootDomain, Value: "reddit.com"}, // duplicate survives: no dedup
			{Type: router.Domain_RootDomain, Value: "redd.it"},
		}},
		{CountryCode: "AVITO", Domain: []*router.Domain{
			{Type: router.Domain_RootDomain, Value: "avito.ru"},
			{Type: router.Domain_Plain, Value: "avito.st"},
		}},
	}}
	merged := &router.GeoSiteList{Entry: []*router.GeoSite{
		{CountryCode: sectionDomains, Domain: []*router.Domain{
			{Type: router.Domain_RootDomain, Value: "example.com"},
		}},
	}}

	var out *router.GeoSiteList
	captureStdout(t, func() {
		out = copyDlcSections(merged, dlc, copySections)
	})

	var names []string
	for _, e := range out.Entry {
		names = append(names, e.CountryCode)
	}
	if want := []string{sectionDomains, sectionGemini, sectionReddit, sectionAvito}; !reflect.DeepEqual(names, want) {
		t.Fatalf("section order = %v, want %v", names, want)
	}
	gem := out.Entry[1]
	if len(gem.Domain) != 2 ||
		gem.Domain[0].Type != router.Domain_Plain || gem.Domain[0].Value != "deepmind.google" ||
		gem.Domain[1].Type != router.Domain_RootDomain || gem.Domain[1].Value != "generativelanguage.googleapis.com" {
		t.Fatalf("GEMINI domains = %+v, want verbatim deepmind list with Type preserved (Plain, RootDomain)", gem.Domain)
	}
	red := out.Entry[2]
	if len(red.Domain) != 3 ||
		red.Domain[0].Type != router.Domain_RootDomain || red.Domain[0].Value != "reddit.com" ||
		red.Domain[1].Type != router.Domain_RootDomain ||
		red.Domain[2].Type != router.Domain_RootDomain || red.Domain[2].Value != "redd.it" {
		t.Fatalf("REDDIT domains = %+v, want verbatim reddit list incl. duplicate (no dedup, Type preserved)", red.Domain)
	}
	av := out.Entry[3]
	if len(av.Domain) != 2 ||
		av.Domain[0].Type != router.Domain_RootDomain || av.Domain[0].Value != "avito.ru" ||
		av.Domain[1].Type != router.Domain_Plain || av.Domain[1].Value != "avito.st" {
		t.Fatalf("AVITO domains = %+v, want verbatim avito list with Type preserved (RootDomain, Plain)", av.Domain)
	}
	if d := out.Entry[0]; len(d.Domain) != 1 || d.Domain[0].Value != "example.com" {
		t.Fatalf("DOMAINS must be untouched, got %+v", d.Domain)
	}
}

func TestCopyDlcSections_MissingSourceCreatesEmptySection(t *testing.T) {
	dlc := &router.GeoSiteList{Entry: []*router.GeoSite{
		{CountryCode: "UNRELATED", Domain: []*router.Domain{
			{Type: router.Domain_RootDomain, Value: "example.org"},
		}},
	}}
	merged := &router.GeoSiteList{}

	var out *router.GeoSiteList
	captureStdout(t, func() {
		out = copyDlcSections(merged, dlc, copySections)
	})

	if len(out.Entry) != len(copySections) {
		t.Fatalf("sections = %d, want %d (one per table row)", len(out.Entry), len(copySections))
	}
	for i, e := range out.Entry {
		if e.CountryCode != copySections[i].target {
			t.Fatalf("section %d name = %q, want %q — the empty section exists so the geosite category is referencable", i, e.CountryCode, copySections[i].target)
		}
		if len(e.Domain) != 0 {
			t.Fatalf("%s: expected empty section when source missing, got %d domains", e.CountryCode, len(e.Domain))
		}
	}
}

func TestCopyDlcSections_NormalizesSourceNames(t *testing.T) {
	dlc := &router.GeoSiteList{Entry: []*router.GeoSite{
		{CountryCode: "Reddit", Domain: []*router.Domain{
			{Type: router.Domain_RootDomain, Value: "reddit.com"},
		}},
	}}
	rules := []copyRule{{sectionReddit, []string{" reddit "}}} // padded, lowercase: must still match "Reddit"

	var out *router.GeoSiteList
	captureStdout(t, func() {
		out = copyDlcSections(&router.GeoSiteList{}, dlc, rules)
	})

	if len(out.Entry) != 1 || out.Entry[0].CountryCode != sectionReddit {
		t.Fatalf("sections = %d, want 1 REDDIT section", len(out.Entry))
	}
	if len(out.Entry[0].Domain) != 1 || out.Entry[0].Domain[0].Value != "reddit.com" {
		t.Fatalf("REDDIT domains = %+v, want source name ToUpper+TrimSpace to match 'Reddit' section", out.Entry[0].Domain)
	}
}
