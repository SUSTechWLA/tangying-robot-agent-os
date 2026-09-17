package pairing

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Certificates, generated here rather than by shelling out to openssl.
//
// `scripts/pair-robot.sh` does the same job with `openssl req` and `openssl x509`.
// That works and is well tested, but it cannot be called from the console — a
// button cannot reasonably shell out to a script that requires `openssl`, a
// temporary directory and five subprocesses, and a failure halfway through leaves
// a half-issued certificate on disk. Doing it in-process means one function that
// either produces the whole set or changes nothing.
//
// The artefacts are deliberately identical in shape to the script's: an ECDSA
// P-256 CA, a 90-day leaf for the robot with DNS and IP subject alternative
// names, a client leaf for the agent. A robot paired either way must be
// indistinguishable afterwards, or the two paths become two kinds of pairing.

// Lifetimes. They match `scripts/pair-robot.sh`, and the reason for each is the
// same: a leaf that outlives its usefulness is a credential nobody rotates.
const (
	// AuthorityLifetime is how long the agent's CA is valid.
	AuthorityLifetime = 10 * 365 * 24 * time.Hour
	// LeafLifetime is how long a robot or agent certificate is valid. Ninety days
	// is short enough that a leaked certificate expires on its own, and long
	// enough not to be a chore.
	LeafLifetime = 90 * 24 * time.Hour
	// RenewalWarning is when an operator should be told a certificate is nearing
	// its end, so renewal is a scheduled act rather than an outage.
	RenewalWarning = 7 * 24 * time.Hour
)

// Authority is the agent's local certificate authority.
//
// The private key never leaves the machine it was created on. That is the whole
// point of the arrangement, and it is why the robot is sent a leaf and a CA
// certificate but never a signing key: a compromised robot can impersonate
// itself, not the authority.
type Authority struct {
	// Certificate is the CA certificate in PEM.
	Certificate []byte
	// Key is the CA private key in PEM. It is never transmitted.
	Key []byte
	// CertificatePath and KeyPath are where they were read from or written to.
	CertificatePath string
	KeyPath         string

	certificate *x509.Certificate
	privateKey  *ecdsa.PrivateKey
}

// LoadOrCreateAuthority reads the agent's CA, creating it on first use.
//
// Creating on first use rather than requiring a setup step is what makes "click
// pair" possible at all: an owner has never heard of a certificate authority and
// should not have to run a command to get one.
//
// An existing CA is never regenerated silently. Replacing it would invalidate
// every robot already paired with it, which is a decision for a person — the
// script has `--new-ca` for exactly that.
func LoadOrCreateAuthority(certificatePath, keyPath string) (*Authority, error) {
	if strings.TrimSpace(certificatePath) == "" || strings.TrimSpace(keyPath) == "" {
		return nil, errors.New("authority needs both a certificate path and a key path")
	}
	certificatePEM, certificateErr := os.ReadFile(filepath.Clean(certificatePath))
	keyPEM, keyErr := os.ReadFile(filepath.Clean(keyPath))
	switch {
	case certificateErr == nil && keyErr == nil:
		return parseAuthority(certificatePEM, keyPEM, certificatePath, keyPath)
	case errors.Is(certificateErr, os.ErrNotExist) && errors.Is(keyErr, os.ErrNotExist):
		return createAuthority(certificatePath, keyPath)
	case certificateErr != nil && !errors.Is(certificateErr, os.ErrNotExist):
		return nil, fmt.Errorf("read authority certificate: %w", certificateErr)
	case keyErr != nil && !errors.Is(keyErr, os.ErrNotExist):
		return nil, fmt.Errorf("read authority key: %w", keyErr)
	default:
		// One half present and the other missing. Generating a replacement would
		// destroy the half that is there and invalidate every paired robot, so
		// this stops and says which file is missing.
		return nil, fmt.Errorf(
			"the authority is incomplete: %s and %s must both exist or neither (%v / %v)",
			certificatePath, keyPath, certificateErr, keyErr)
	}
}

func createAuthority(certificatePath, keyPath string) (*Authority, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generate authority key: %w", err)
	}
	serial, err := randomSerial()
	if err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			CommonName:   "Tangying Robot Local CA",
			Organization: []string{"Tangying Robot Agent OS"},
		},
		NotBefore:             now.Add(-time.Hour), // tolerate a slightly behind clock
		NotAfter:              now.Add(AuthorityLifetime),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
		IsCA:                  true,
		MaxPathLen:            0,
		MaxPathLenZero:        true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		return nil, fmt.Errorf("create authority certificate: %w", err)
	}
	certificatePEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM, err := encodeKey(key)
	if err != nil {
		return nil, err
	}
	if err := writeFileExclusive(certificatePath, certificatePEM, 0o644); err != nil {
		return nil, err
	}
	if err := writeFileExclusive(keyPath, keyPEM, 0o600); err != nil {
		return nil, err
	}
	return parseAuthority(certificatePEM, keyPEM, certificatePath, keyPath)
}

func parseAuthority(certificatePEM, keyPEM []byte, certificatePath, keyPath string) (*Authority, error) {
	certificate, err := parseCertificate(certificatePEM)
	if err != nil {
		return nil, fmt.Errorf("parse authority certificate: %w", err)
	}
	if !certificate.IsCA {
		return nil, errors.New("the authority certificate is not a CA certificate")
	}
	key, err := parseKey(keyPEM)
	if err != nil {
		return nil, fmt.Errorf("parse authority key: %w", err)
	}
	authority := &Authority{
		Certificate: certificatePEM, Key: keyPEM,
		CertificatePath: certificatePath, KeyPath: keyPath,
		certificate: certificate, privateKey: key,
	}
	// A CA that is about to expire cannot issue anything useful, and finding that
	// out at the moment of pairing is the worst time to find it out.
	if remaining := time.Until(certificate.NotAfter); remaining < RenewalWarning {
		return nil, fmt.Errorf(
			"the authority certificate expires in %s; renew it before pairing robots",
			remaining.Round(time.Hour))
	}
	return authority, nil
}

// RobotMaterial issues the certificate set one robot needs.
//
// `host` and `ip` become the certificate's subject alternative names, because a
// certificate that does not name the address the agent actually connects to
// fails at handshake time with a message about hostnames that tells an owner
// nothing.
func (a *Authority) RobotMaterial(host, ip string) (Material, error) {
	trimmedHost := strings.TrimSpace(host)
	if trimmedHost == "" {
		return Material{}, errors.New("robot certificate needs a host name")
	}
	serverCert, serverKey, err := a.issueLeaf(issueRequest{
		commonName: trimmedHost,
		usage:      x509.ExtKeyUsageServerAuth,
		dnsNames:   []string{trimmedHost},
		ipAddress:  strings.TrimSpace(ip),
	})
	if err != nil {
		return Material{}, fmt.Errorf("issue robot certificate: %w", err)
	}
	return Material{
		CA:         string(a.Certificate),
		ServerCert: serverCert,
		ServerKey:  serverKey,
	}, nil
}

// AgentCertificate issues the agent's own client certificate.
//
// It is what makes the mutual half of mutual TLS: without it the robot would be
// authenticating to the agent and the agent to nobody, which is a connection the
// robot has no reason to accept.
func (a *Authority) AgentCertificate() (certificatePEM, keyPEM []byte, err error) {
	certificate, key, err := a.issueLeaf(issueRequest{
		commonName: "tangying-local-agent",
		usage:      x509.ExtKeyUsageClientAuth,
		dnsNames:   []string{"tangying-local-agent"},
	})
	if err != nil {
		return nil, nil, fmt.Errorf("issue agent certificate: %w", err)
	}
	return []byte(certificate), []byte(key), nil
}

type issueRequest struct {
	commonName string
	usage      x509.ExtKeyUsage
	dnsNames   []string
	ipAddress  string
}

func (a *Authority) issueLeaf(request issueRequest) (string, string, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return "", "", fmt.Errorf("generate key: %w", err)
	}
	serial, err := randomSerial()
	if err != nil {
		return "", "", err
	}
	now := time.Now().UTC()
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			CommonName:   request.commonName,
			Organization: []string{"Tangying Robot Agent OS"},
		},
		NotBefore:   now.Add(-time.Hour),
		NotAfter:    now.Add(LeafLifetime),
		KeyUsage:    x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage: []x509.ExtKeyUsage{request.usage},
		// Not a CA, and marked critical so a leaf cannot be used to sign: a leaf
		// that can sign turns one compromised robot into an authority.
		BasicConstraintsValid: true,
		IsCA:                  false,
	}
	if len(request.dnsNames) > 0 {
		template.DNSNames = request.dnsNames
	}
	if request.ipAddress != "" {
		parsed := net.ParseIP(request.ipAddress)
		if parsed == nil {
			return "", "", fmt.Errorf("%q is not an IP address", request.ipAddress)
		}
		template.IPAddresses = []net.IP{parsed}
	}
	der, err := x509.CreateCertificate(rand.Reader, template, a.certificate, &key.PublicKey, a.privateKey)
	if err != nil {
		return "", "", fmt.Errorf("sign certificate: %w", err)
	}
	keyPEM, err := encodeKey(key)
	if err != nil {
		return "", "", err
	}
	certificatePEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	// Verified before it is handed over. A certificate this side cannot verify
	// against its own authority would fail on the robot at handshake time, where
	// the error is a TLS alert nobody can read.
	if _, err := verifyAgainst(a.certificate, certificatePEM, request.usage); err != nil {
		return "", "", fmt.Errorf("the certificate just issued does not verify: %w", err)
	}
	return string(certificatePEM), string(keyPEM), nil
}

// CertificateExpiry reports when a certificate stops being valid.
//
// It exists so readiness can warn about a certificate before it expires rather
// than after: the failure mode of an expired certificate is a robot that
// suddenly cannot be reached, and nothing about that message says "certificate".
func CertificateExpiry(certificatePEM []byte) (time.Time, error) {
	certificate, err := parseCertificate(certificatePEM)
	if err != nil {
		return time.Time{}, err
	}
	return certificate.NotAfter, nil
}

func parseCertificate(certificatePEM []byte) (*x509.Certificate, error) {
	block, _ := pem.Decode(certificatePEM)
	if block == nil || block.Type != "CERTIFICATE" {
		return nil, errors.New("no PEM certificate block found")
	}
	return x509.ParseCertificate(block.Bytes)
}

func parseKey(keyPEM []byte) (*ecdsa.PrivateKey, error) {
	block, _ := pem.Decode(keyPEM)
	if block == nil {
		return nil, errors.New("no PEM key block found")
	}
	if key, err := x509.ParseECPrivateKey(block.Bytes); err == nil {
		return key, nil
	}
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, errors.New("the key is neither SEC 1 nor PKCS#8")
	}
	key, ok := parsed.(*ecdsa.PrivateKey)
	if !ok {
		return nil, errors.New("the key is not an ECDSA key")
	}
	return key, nil
}

func encodeKey(key *ecdsa.PrivateKey) ([]byte, error) {
	der, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return nil, fmt.Errorf("encode key: %w", err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: der}), nil
}

func verifyAgainst(authority *x509.Certificate, certificatePEM []byte, usage x509.ExtKeyUsage) (*x509.Certificate, error) {
	certificate, err := parseCertificate(certificatePEM)
	if err != nil {
		return nil, err
	}
	pool := x509.NewCertPool()
	pool.AddCert(authority)
	if _, err := certificate.Verify(x509.VerifyOptions{
		Roots:     pool,
		KeyUsages: []x509.ExtKeyUsage{usage},
	}); err != nil {
		return nil, err
	}
	return certificate, nil
}

func randomSerial() (*big.Int, error) {
	limit := new(big.Int).Lsh(big.NewInt(1), 128)
	serial, err := rand.Int(rand.Reader, limit)
	if err != nil {
		return nil, fmt.Errorf("serial: %w", err)
	}
	return serial, nil
}

// writeFileExclusive writes a file that must not already exist.
//
// The CA is created once. A create that overwrote would silently invalidate every
// robot paired with the previous authority, and the symptom would be a fleet that
// stopped trusting its agent.
func writeFileExclusive(path string, content []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("create directory for %s: %w", path, err)
	}
	file, err := os.OpenFile(filepath.Clean(path), os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
	if err != nil {
		return fmt.Errorf("create %s: %w", path, err)
	}
	defer file.Close()
	if _, err := file.Write(content); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return file.Sync()
}
