package main

import (
	"testing"
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
