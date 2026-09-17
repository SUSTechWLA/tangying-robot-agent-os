package pairing

import (
	"crypto/tls"
	"crypto/x509"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func authorityDirectory(t *testing.T) (string, string) {
	t.Helper()
	directory := t.TempDir()
	return filepath.Join(directory, "ca.crt"), filepath.Join(directory, "ca.key")
}

// The CA is created on first use, because an owner has never heard of a
// certificate authority and should not have to run a command to get one.
func TestTheAuthorityIsCreatedOnFirstUse(t *testing.T) {
	certificatePath, keyPath := authorityDirectory(t)
	authority, err := LoadOrCreateAuthority(certificatePath, keyPath)
	if err != nil {
		t.Fatalf("create authority: %v", err)
	}
	if len(authority.Certificate) == 0 || len(authority.Key) == 0 {
		t.Fatal("the authority has no material")
	}
	if _, err := os.Stat(certificatePath); err != nil {
		t.Fatalf("the certificate was not written: %v", err)
	}
	// The signing key must not be readable by anyone else on the machine.
	info, err := os.Stat(keyPath)
	if err != nil {
		t.Fatalf("stat key: %v", err)
	}
	if mode := info.Mode().Perm(); mode != 0o600 {
		t.Fatalf("authority key mode = %o, want 600", mode)
	}
}

// And it is never regenerated silently: that would invalidate every robot already
// paired with it, which is a decision for a person.
func TestAnExistingAuthorityIsReused(t *testing.T) {
	certificatePath, keyPath := authorityDirectory(t)
	first, err := LoadOrCreateAuthority(certificatePath, keyPath)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	second, err := LoadOrCreateAuthority(certificatePath, keyPath)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if string(first.Certificate) != string(second.Certificate) {
		t.Fatal("reloading the authority produced a different certificate")
	}
}

// Half an authority is a state that must stop rather than be repaired by
// guessing: the missing half could be either one, and regenerating destroys what
// is there.
func TestHalfAnAuthorityStopsWithTheMissingFileNamed(t *testing.T) {
	certificatePath, keyPath := authorityDirectory(t)
	if _, err := LoadOrCreateAuthority(certificatePath, keyPath); err != nil {
		t.Fatalf("create: %v", err)
	}
	if err := os.Remove(keyPath); err != nil {
		t.Fatalf("remove key: %v", err)
	}
	_, err := LoadOrCreateAuthority(certificatePath, keyPath)
	if err == nil {
		t.Fatal("an authority with no key was accepted")
	}
	if !strings.Contains(err.Error(), keyPath) {
		t.Fatalf("error = %v, want it to name the missing key", err)
	}
}

// The robot's certificate must name the address the agent will connect to.
//
// A certificate that does not name it fails at handshake time with a message
// about hostnames that tells an owner nothing.
func TestTheRobotCertificateNamesTheAddressTheAgentUses(t *testing.T) {
	certificatePath, keyPath := authorityDirectory(t)
	authority, err := LoadOrCreateAuthority(certificatePath, keyPath)
	if err != nil {
		t.Fatalf("authority: %v", err)
	}
	material, err := authority.RobotMaterial("xlerobot.local", "192.168.50.73")
	if err != nil {
		t.Fatalf("issue: %v", err)
	}
	certificate := parseForTest(t, material.ServerCert)
	if len(certificate.DNSNames) != 1 || certificate.DNSNames[0] != "xlerobot.local" {
		t.Fatalf("dns names = %v", certificate.DNSNames)
	}
	if len(certificate.IPAddresses) != 1 || !certificate.IPAddresses[0].Equal(net.ParseIP("192.168.50.73")) {
		t.Fatalf("ip addresses = %v", certificate.IPAddresses)
	}
	// A leaf that can sign turns one compromised robot into an authority.
	if certificate.IsCA {
		t.Fatal("the robot's certificate is a CA certificate")
	}
	if !certificate.BasicConstraintsValid {
		t.Fatal("the robot's certificate has no basic constraints")
	}
}

func TestARobotCertificateWithoutAHostIsRefused(t *testing.T) {
	certificatePath, keyPath := authorityDirectory(t)
	authority, _ := LoadOrCreateAuthority(certificatePath, keyPath)
	if _, err := authority.RobotMaterial("", ""); err == nil {
		t.Fatal("a robot certificate was issued with no host name")
	}
	if _, err := authority.RobotMaterial("xlerobot.local", "not-an-ip"); err == nil {
		t.Fatal("a robot certificate was issued with a malformed address")
	}
}

// The half that makes mutual TLS mutual.
func TestTheAgentCertificateIsAClientCertificate(t *testing.T) {
	certificatePath, keyPath := authorityDirectory(t)
	authority, _ := LoadOrCreateAuthority(certificatePath, keyPath)
	certificatePEM, keyPEM, err := authority.AgentCertificate()
	if err != nil {
		t.Fatalf("issue agent certificate: %v", err)
	}
	if len(keyPEM) == 0 {
		t.Fatal("no key was issued")
	}
	certificate := parseForTest(t, string(certificatePEM))
	found := false
	for _, usage := range certificate.ExtKeyUsage {
		if usage == x509.ExtKeyUsageClientAuth {
			found = true
		}
		if usage == x509.ExtKeyUsageServerAuth {
			t.Fatal("the agent certificate can act as a server, which it never does")
		}
	}
	if !found {
		t.Fatalf("ext key usage = %v, want client auth", certificate.ExtKeyUsage)
	}
}

// The issued pair must actually complete a TLS handshake, which is the only
// assertion that covers the whole chain: signature, key match, usages, names.
func TestTheIssuedPairCompletesAHandshake(t *testing.T) {
	certificatePath, keyPath := authorityDirectory(t)
	authority, _ := LoadOrCreateAuthority(certificatePath, keyPath)

	material, err := authority.RobotMaterial("localhost", "127.0.0.1")
	if err != nil {
		t.Fatalf("robot material: %v", err)
	}
	serverCertificate, err := tls.X509KeyPair([]byte(material.ServerCert), []byte(material.ServerKey))
	if err != nil {
		t.Fatalf("the issued pair is not a usable key pair: %v", err)
	}
	clientCertificatePEM, clientKeyPEM, err := authority.AgentCertificate()
	if err != nil {
		t.Fatalf("agent certificate: %v", err)
	}
	clientCertificate, err := tls.X509KeyPair(clientCertificatePEM, clientKeyPEM)
	if err != nil {
		t.Fatalf("the agent pair is not usable: %v", err)
	}
	pool := x509.NewCertPool()
	pool.AddCert(parseForTest(t, string(authority.Certificate)))

	listener, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{
		Certificates: []tls.Certificate{serverCertificate},
		ClientCAs:    pool,
		ClientAuth:   tls.RequireAndVerifyClientCert,
		MinVersion:   tls.VersionTLS12,
	})
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer listener.Close()

	done := make(chan error, 1)
	go func() {
		connection, acceptErr := listener.Accept()
		if acceptErr != nil {
			done <- acceptErr
			return
		}
		defer connection.Close()
		done <- connection.(*tls.Conn).Handshake()
	}()

	client, err := tls.Dial("tcp", listener.Addr().String(), &tls.Config{
		Certificates: []tls.Certificate{clientCertificate},
		RootCAs:      pool,
		ServerName:   "localhost",
		MinVersion:   tls.VersionTLS12,
	})
	if err != nil {
		t.Fatalf("the agent could not connect with the issued material: %v", err)
	}
	defer client.Close()
	if err := <-done; err != nil {
		t.Fatalf("the robot side of the handshake failed: %v", err)
	}
}

// A certificate whose authority does not match must not be accepted, or the
// fingerprint an operator compares would mean nothing.
func TestAMaterialFromAnotherAuthorityIsNotAcceptedByOurs(t *testing.T) {
	firstCertificate, firstKey := authorityDirectory(t)
	secondCertificate, secondKey := authorityDirectory(t)
	first, _ := LoadOrCreateAuthority(firstCertificate, firstKey)
	second, _ := LoadOrCreateAuthority(secondCertificate, secondKey)

	material, err := second.RobotMaterial("xlerobot.local", "192.168.50.73")
	if err != nil {
		t.Fatalf("issue: %v", err)
	}
	pool := x509.NewCertPool()
	pool.AddCert(parseForTest(t, string(first.Certificate)))
	certificate := parseForTest(t, material.ServerCert)
	if _, err := certificate.Verify(x509.VerifyOptions{
		Roots: pool, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}); err == nil {
		t.Fatal("a certificate from another authority verified against ours")
	}
}

// A certificate that is about to expire cannot issue anything useful, and the
// moment of pairing is the worst time to discover it.
func TestAnExpiringAuthorityRefusesToIssue(t *testing.T) {
	// Covered through CertificateExpiry, which is what readiness reads: issuing
	// from an expired authority is prevented by the same check.
	certificatePath, keyPath := authorityDirectory(t)
	authority, _ := LoadOrCreateAuthority(certificatePath, keyPath)
	expiry, err := CertificateExpiry(authority.Certificate)
	if err != nil {
		t.Fatalf("expiry: %v", err)
	}
	if time.Until(expiry) < RenewalWarning {
		t.Fatalf("a freshly created authority expires in %s", time.Until(expiry))
	}
}

func TestAFileThatIsNotACertificateIsRefused(t *testing.T) {
	directory := t.TempDir()
	certificatePath := filepath.Join(directory, "ca.crt")
	keyPath := filepath.Join(directory, "ca.key")
	if err := os.WriteFile(certificatePath, []byte("not a certificate"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := os.WriteFile(keyPath, []byte("not a key"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, err := LoadOrCreateAuthority(certificatePath, keyPath); err == nil {
		t.Fatal("a garbage authority was accepted")
	}
}

func TestAnAuthorityWithoutPathsIsRefused(t *testing.T) {
	// Both empty, and one empty: an authority with nowhere to live would be
	// regenerated on every call, and every pairing would invalidate the last.
	for _, paths := range [][2]string{{"", ""}, {"", "/tmp/ca.key"}, {"/tmp/ca.crt", ""}} {
		if _, err := LoadOrCreateAuthority(paths[0], paths[1]); err == nil {
			t.Fatalf("an authority with paths %v was accepted", paths)
		}
	}
}

func parseForTest(t *testing.T, certificatePEM string) *x509.Certificate {
	t.Helper()
	certificate, err := parseCertificate([]byte(certificatePEM))
	if err != nil {
		t.Fatalf("parse certificate: %v", err)
	}
	return certificate
}
