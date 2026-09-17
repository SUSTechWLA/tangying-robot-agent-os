package pairing

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"net"
	"strings"
	"testing"
	"time"
)

// pairingHarness runs a robot that answers like the Python one.
//
// It implements the robot's side of the protocol in Go so the client can be
// tested without a subprocess, and the *cross-language* agreement is covered
// separately by the fixture. Two tests, two jobs: this one is about what the
// client does with each answer, the fixture is about whether the two languages
// speak the same protocol at all.
type pairingHarness struct {
	listener  net.Listener
	code      string
	robotID   string
	answer    func(material Material) ResultBody
	installed *Material
	requests  int
}

func startPairingHarness(t *testing.T, code, robotID string, answer func(Material) ResultBody) *pairingHarness {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	harness := &pairingHarness{listener: listener, code: code, robotID: robotID, answer: answer}
	go func() {
		for {
			connection, err := listener.Accept()
			if err != nil {
				return
			}
			go harness.serve(connection)
		}
	}()
	t.Cleanup(func() { listener.Close() })
	return harness
}

func (h *pairingHarness) port() int { return h.listener.Addr().(*net.TCPAddr).Port }

func (h *pairingHarness) serve(connection net.Conn) {
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(10 * time.Second))
	h.requests++
	request, err := ReadFrame(connection)
	if err != nil {
		return
	}
	material, envelope, err := DecodeRequest(h.code, request)
	if err != nil {
		return // a refusal is a closed connection with no answer
	}
	h.installed = &material
	salt, err := saltOf(request)
	if err != nil {
		return
	}
	nonce, err := NewNonce()
	if err != nil {
		return
	}
	body := h.answer(material)
	if body.Status == "" {
		body.Status = "paired"
	}
	answer, err := EncodeResult(h.code, salt, nonce, envelope.RobotID, body)
	if err != nil {
		return
	}
	_ = WriteFrame(connection, answer)
}

func testAuthority(t *testing.T) *Authority {
	t.Helper()
	directory := t.TempDir()
	authority, err := LoadOrCreateAuthority(directory+"/ca.crt", directory+"/ca.key")
	if err != nil {
		t.Fatalf("authority: %v", err)
	}
	return authority
}

// The whole point: no SSH, no hostname typed by hand, one call.
func TestARobotIsPairedOverTheNetworkWithoutSSH(t *testing.T) {
	harness := startPairingHarness(t, "4F2K-9QW7", "xlerobot-0001", func(Material) ResultBody {
		return ResultBody{Status: "paired"}
	})
	authority := testAuthority(t)
	client := &Client{Port: harness.port()}

	result, err := client.Pair(context.Background(), "4F2K-9QW7", Address{
		RobotID: "xlerobot-0001", Host: "127.0.0.1", IP: "127.0.0.1", Authority: authority,
	})
	if err != nil {
		t.Fatalf("pair: %v", err)
	}
	if result.RobotID != "xlerobot-0001" {
		t.Fatalf("robot id = %q", result.RobotID)
	}
	// And what the robot received is a usable certificate, not just some bytes.
	if harness.installed == nil {
		t.Fatal("the robot installed nothing")
	}
	if _, err := tls.X509KeyPair(
		[]byte(harness.installed.ServerCert), []byte(harness.installed.ServerKey),
	); err != nil {
		t.Fatalf("the robot was given an unusable key pair: %v", err)
	}
	if harness.installed.CA != string(authority.Certificate) {
		t.Fatal("the robot was given a different authority than the one that signed it")
	}
}

// A code written the way a person types it still works.
func TestTheCodeMayBeTypedInAnySpelling(t *testing.T) {
	harness := startPairingHarness(t, "4F2K9QW7", "xlerobot-0001", func(Material) ResultBody {
		return ResultBody{Status: "paired"}
	})
	client := &Client{Port: harness.port()}
	for _, spelling := range []string{"4F2K-9QW7", "4f2k9qw7", " 4F2K 9QW7 "} {
		if _, err := client.Pair(context.Background(), spelling, Address{
			RobotID: "xlerobot-0001", Host: "127.0.0.1", IP: "127.0.0.1", Authority: testAuthority(t),
		}); err != nil {
			t.Fatalf("pairing with %q failed: %v", spelling, err)
		}
	}
}

// A wrong code fails with something an operator can act on.
func TestAWrongCodeSaysWhatToDoAboutIt(t *testing.T) {
	harness := startPairingHarness(t, "4F2K-9QW7", "xlerobot-0001", func(Material) ResultBody {
		return ResultBody{Status: "paired"}
	})
	client := &Client{Port: harness.port()}
	_, err := client.Pair(context.Background(), "AAAA-BBBB", Address{
		RobotID: "xlerobot-0001", Host: "127.0.0.1", IP: "127.0.0.1", Authority: testAuthority(t),
	})
	if err == nil {
		t.Fatal("a wrong code paired")
	}
	// The message has to say what the code is and that it is single-use; an
	// operator seeing "connection closed" would have no idea what to try.
	if !strings.Contains(err.Error(), "配对码") {
		t.Fatalf("error = %v, want it to mention the pairing code", err)
	}
}

// Pairing a robot that is not offering says so, rather than looking like a bug.
func TestAPortWithNothingBehindItExplainsItself(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()

	client := &Client{Port: port, Timeout: 2 * time.Second}
	_, err = client.Pair(context.Background(), "4F2K-9QW7", Address{
		RobotID: "xlerobot-0001", Host: "127.0.0.1", IP: "127.0.0.1", Authority: testAuthority(t),
	})
	if !errors.Is(err, ErrUnreachable) {
		t.Fatalf("error = %v, want ErrUnreachable", err)
	}
	if !strings.Contains(err.Error(), "配对窗口") {
		t.Fatalf("error = %v, want it to explain the likely cause", err)
	}
}

// A refusal from the robot carries the robot's own sentence.
func TestARefusalCarriesTheRobotsReason(t *testing.T) {
	harness := startPairingHarness(t, "4F2K-9QW7", "xlerobot-0001", func(Material) ResultBody {
		return ResultBody{Status: "refused", Detail: "这台机器人已经配过对了，请先在机器人上重新生成配对码"}
	})
	client := &Client{Port: harness.port()}
	_, err := client.Pair(context.Background(), "4F2K-9QW7", Address{
		RobotID: "xlerobot-0001", Host: "127.0.0.1", IP: "127.0.0.1", Authority: testAuthority(t),
	})
	if !errors.Is(err, ErrRobotRefused) {
		t.Fatalf("error = %v, want ErrRobotRefused", err)
	}
	if !strings.Contains(err.Error(), "重新生成配对码") {
		t.Fatalf("error = %v, want the robot's own reason", err)
	}
}

// Nothing is sent when there is nothing to send with.
func TestPairingIsRefusedWithoutACodeOrAnAddress(t *testing.T) {
	client := &Client{}
	if _, err := client.Pair(context.Background(), "", Address{
		RobotID: "r", Host: "127.0.0.1", Authority: testAuthority(t),
	}); err == nil {
		t.Fatal("pairing with no code was attempted")
	}
	if _, err := client.Pair(context.Background(), "4F2K-9QW7", Address{
		RobotID: "", Host: "127.0.0.1", Authority: testAuthority(t),
	}); err == nil {
		t.Fatal("pairing with no robot was attempted")
	}
}

// The certificate the robot receives names the address the agent dialled, not the
// address the announcement claimed: the announcement is unauthenticated, and a
// certificate built from it would be built from an attacker's input.
func TestTheCertificateNamesTheAddressActuallyDialled(t *testing.T) {
	harness := startPairingHarness(t, "4F2K-9QW7", "xlerobot-0001", func(Material) ResultBody {
		return ResultBody{Status: "paired"}
	})
	client := &Client{Port: harness.port()}
	if _, err := client.Pair(context.Background(), "4F2K-9QW7", Address{
		RobotID: "xlerobot-0001", Host: "127.0.0.1", IP: "127.0.0.1", Authority: testAuthority(t),
	}); err != nil {
		t.Fatalf("pair: %v", err)
	}
	certificate, err := parseCertificate([]byte(harness.installed.ServerCert))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	pool := x509.NewCertPool()
	pool.AddCert(certificate)
	if len(certificate.DNSNames) != 1 || certificate.DNSNames[0] != "127.0.0.1" {
		t.Fatalf("dns names = %v, want the dialled host", certificate.DNSNames)
	}
}
