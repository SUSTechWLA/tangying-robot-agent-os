package discovery_test

import (
	"context"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/discovery"
)

// The fixture is the contract with the robot.
//
// The robot runtime is Python and this reader is Go, so nothing in either
// language's compiler can keep the two in step. A file both sides are tested
// against can: the Python side asserts that what it emits is byte-identical to
// this, and this side asserts it can read it. A change to one side that the
// other does not expect fails a test here instead of failing silently on a
// customer's network.
const fixturePath = "../../tests/contract/robot_announcement.json"

func readFixture(t *testing.T) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Clean(fixturePath))
	if err != nil {
		t.Fatalf("read the robot announcement fixture: %v", err)
	}
	return data
}

// TestTheAnnouncementFixtureMakesSenseAsJSON fails with a readable message when
// the fixture is missing rather than when a health check mysteriously reports a
// malformed robot.
func TestTheAnnouncementFixtureMakesSenseAsJSON(t *testing.T) {
	var decoded map[string]any
	if err := json.Unmarshal(readFixture(t), &decoded); err != nil {
		t.Fatalf("the fixture is not JSON: %v", err)
	}
	for _, key := range []string{"topic", "version", "robotId", "hostname", "address", "adapter", "pairingState", "sentAt", "enrollmentPort"} {
		if _, present := decoded[key]; !present {
			t.Fatalf("the fixture is missing %q, so it does not pin the field: %#v", key, decoded)
		}
	}
}

// TestTheReaderAcceptsWhatTheRobotSends is the cross-language assertion.
//
// Verified by corrupting the fixture: renaming `robotId` to `robot_identifier`
// fails here, and the Python byte-equality test fails on the same edit. One
// rename slips through this side alone — Go's JSON matching is case-insensitive,
// so `robotID` still populates RobotID — which is why the fixture is asserted on
// both sides rather than on this one.
func TestTheReaderAcceptsWhatTheRobotSends(t *testing.T) {
	announcement, err := discovery.DecodeAnnouncement(readFixture(t))
	if err != nil {
		t.Fatalf("the reader refused the payload the robot produces: %v", err)
	}
	if announcement.RobotID != "xlerobot-0001" {
		t.Fatalf("robotId = %q", announcement.RobotID)
	}
	if announcement.Address != "192.168.50.73:50051" {
		t.Fatalf("address = %q", announcement.Address)
	}
	if announcement.Hostname != "xlerobot.local" {
		t.Fatalf("hostname = %q", announcement.Hostname)
	}
	if announcement.Adapter != "xlerobot" {
		t.Fatalf("adapter = %q", announcement.Adapter)
	}
	if announcement.PairingState != discovery.PairingOpen {
		t.Fatalf("pairingState = %q, want %q", announcement.PairingState, discovery.PairingOpen)
	}
	if announcement.CapabilityCount != 12 {
		t.Fatalf("capabilityCount = %d", announcement.CapabilityCount)
	}
	// The port the pairing request must be sent to. An agent that assumed the
	// default would report "the robot is not offering to be paired" while the
	// robot waited on another port.
	if announcement.EnrollmentPort != 45872 {
		t.Fatalf("enrollmentPort = %d, want the announced pairing port", announcement.EnrollmentPort)
	}
	// The timestamp is the field most likely to drift between two languages:
	// Go's time.Time rejects a format that Python's strftime produces happily.
	if announcement.SentAt.IsZero() {
		t.Fatal("sentAt did not parse, so a robot would appear to have announced at the zero time")
	}
	if got := announcement.SentAt.UTC().Format(time.RFC3339); got != "2025-09-16T05:20:00Z" {
		t.Fatalf("sentAt parsed as %s", got)
	}
}

func TestAnAnnouncementIsRefusedWhenItIsNotOne(t *testing.T) {
	for name, payload := range map[string]string{
		"empty":         ``,
		"not json":      `hello robot`,
		"json array":    `[1,2,3]`,
		"wrong topic":   `{"topic":"something.else","version":1,"robotId":"r","address":"a:1"}`,
		"no robot id":   `{"topic":"tangying.robot.announce","version":1,"address":"a:1"}`,
		"no address":    `{"topic":"tangying.robot.announce","version":1,"robotId":"r"}`,
		"blank address": `{"topic":"tangying.robot.announce","version":1,"robotId":"r","address":"  "}`,
	} {
		if _, err := discovery.DecodeAnnouncement([]byte(payload)); err == nil {
			t.Fatalf("%s was accepted as a robot announcement", name)
		}
	}
}

// A robot from a newer firmware must not be half-understood.
//
// The fields that changed may be exactly the ones that decide what to do with
// it, so an unknown version is refused rather than read on a best-effort basis.
func TestAnAnnouncementFromAnotherProtocolVersionIsRefused(t *testing.T) {
	_, err := discovery.DecodeAnnouncement([]byte(
		`{"topic":"tangying.robot.announce","version":99,"robotId":"r","address":"a:1"}`))
	if err == nil {
		t.Fatal("an announcement from an unknown protocol version was accepted")
	}
	if !errorsIs(err, discovery.ErrUnsupportedVersion) {
		t.Fatalf("error = %v, want ErrUnsupportedVersion so a version mismatch is reportable", err)
	}
}

// A payload too large to be an announcement is refused before it is parsed.
func TestAnOversizedDatagramIsRefused(t *testing.T) {
	oversized := make([]byte, discovery.MaxAnnouncementSize+1)
	for index := range oversized {
		oversized[index] = 'x'
	}
	if _, err := discovery.DecodeAnnouncement(oversized); err == nil {
		t.Fatal("an oversized datagram was accepted")
	}
}

// A robot that says nothing about pairing is not assumed to be pairable.
func TestAnUnknownPairingStateIsNotReadAsAnInvitation(t *testing.T) {
	announcement, err := discovery.DecodeAnnouncement([]byte(
		`{"topic":"tangying.robot.announce","version":1,"robotId":"r","address":"a:1"}`))
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if announcement.PairingState == discovery.PairingOpen {
		t.Fatal("a robot that announced no pairing state was treated as open to pairing")
	}
}

// sendAnnouncement writes one payload to the listener and returns when it has
// been written. Delivery is not synchronous, so callers wait for the effect.
func sendAnnouncement(t *testing.T, payload []byte) {
	t.Helper()
	connection, err := net.Dial("udp4", "127.0.0.1:45899")
	if err != nil {
		t.Fatalf("dial the test listener: %v", err)
	}
	defer connection.Close()
	if _, err := connection.Write(payload); err != nil {
		t.Fatalf("send the announcement: %v", err)
	}
}

func payloadFor(t *testing.T, robotID, address, state string) []byte {
	t.Helper()
	encoded, err := discovery.Announcement{
		RobotID: robotID, Hostname: robotID + ".local", Address: address,
		Adapter: "xlerobot", PairingState: state, CapabilityCount: 3,
	}.Encode()
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	return encoded
}

// startListener runs a listener on a test port and waits until it is bound.
func startListener(t *testing.T, configure func(*discovery.Listener)) *discovery.Listener {
	t.Helper()
	listener := discovery.NewListener()
	listener.Port = 45899
	listener.Retention = 2 * time.Second
	if configure != nil {
		configure(listener)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		if err := listener.Listen(ctx); err != nil && ctx.Err() == nil {
			t.Errorf("listen: %v", err)
		}
	}()
	t.Cleanup(func() {
		cancel()
		_ = listener.Stop()
		select {
		case <-done:
		case <-time.After(3 * time.Second):
			t.Error("the listener did not stop")
		}
	})
	// Wait for the socket rather than sleeping a fixed time: a fixed sleep is
	// either slower than needed or flaky under load.
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		connection, err := net.Dial("udp4", "127.0.0.1:45899")
		if err == nil {
			connection.Close()
			return listener
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("the listener never bound its port")
	return nil
}

func waitFor(t *testing.T, what string, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if condition() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

// TestARobotOnTheNetworkAppearsWithoutBeingConfigured is the whole point.
func TestARobotOnTheNetworkAppearsWithoutBeingConfigured(t *testing.T) {
	listener := startListener(t, nil)
	sendAnnouncement(t, payloadFor(t, "xlerobot-0001", "192.168.50.73:50051", discovery.PairingOpen))

	waitFor(t, "the robot to appear", func() bool { return len(listener.Robots()) == 1 })
	robot := listener.Robots()[0]
	if robot.RobotID != "xlerobot-0001" || robot.Address != "192.168.50.73:50051" {
		t.Fatalf("robot = %#v", robot)
	}
	// The source address is recorded separately from the announced one, so a
	// disagreement between what the robot believes and what the network says is
	// visible instead of being silently resolved in favour of one of them.
	if robot.SourceIP != "127.0.0.1" {
		t.Fatalf("source ip = %q, want where the datagram actually came from", robot.SourceIP)
	}
	if !robot.PairingCodeRequired {
		t.Fatal("a robot announcing itself as open to pairing was not flagged as needing its code")
	}
}

// A robot that stops announcing disappears on its own.
func TestARobotThatStopsAnnouncingDisappears(t *testing.T) {
	listener := startListener(t, func(l *discovery.Listener) { l.Retention = 150 * time.Millisecond })
	sendAnnouncement(t, payloadFor(t, "xlerobot-0001", "192.168.50.73:50051", discovery.PairingPaired))
	waitFor(t, "the robot to appear", func() bool { return len(listener.Robots()) == 1 })

	// Nothing further arrives. Retention is about elapsed time, not traffic, so
	// this only passes if the sweep runs on its own.
	waitFor(t, "the robot to age out", func() bool { return len(listener.Robots()) == 0 })
}

// Two robots are two entries, keyed by identity rather than by address: a robot
// that changes address is still the same robot.
func TestARobotThatChangesAddressStaysOneEntry(t *testing.T) {
	listener := startListener(t, nil)
	sendAnnouncement(t, payloadFor(t, "xlerobot-0001", "192.168.50.73:50051", discovery.PairingPaired))
	waitFor(t, "the first announcement", func() bool { return len(listener.Robots()) == 1 })
	first := listener.Robots()[0]

	sendAnnouncement(t, payloadFor(t, "xlerobot-0001", "192.168.50.99:50051", discovery.PairingPaired))
	waitFor(t, "the address to change", func() bool {
		robots := listener.Robots()
		return len(robots) == 1 && robots[0].Address == "192.168.50.99:50051"
	})
	// How long this listener has known the robot is not reset by an address
	// change, or a robot would always look as though it had just appeared.
	if !listener.Robots()[0].FirstSeen.Equal(first.FirstSeen) {
		t.Fatal("an address change reset when the robot was first seen")
	}
}

// The list leads with the robots an operator can act on.
func TestRobotsOpenToPairingAreListedFirst(t *testing.T) {
	listener := startListener(t, nil)
	sendAnnouncement(t, payloadFor(t, "paired-robot", "192.168.50.10:50051", discovery.PairingPaired))
	waitFor(t, "the paired robot", func() bool { return len(listener.Robots()) == 1 })
	sendAnnouncement(t, payloadFor(t, "new-robot", "192.168.50.11:50051", discovery.PairingOpen))
	waitFor(t, "both robots", func() bool { return len(listener.Robots()) == 2 })

	if first := listener.Robots()[0]; first.RobotID != "new-robot" {
		t.Fatalf("list leads with %q, want the robot that is open to pairing", first.RobotID)
	}
}

// Foreign traffic on the port is counted, not logged per packet and not mistaken
// for a robot.
func TestUnreadableTrafficIsCountedRatherThanTreatedAsARobot(t *testing.T) {
	listener := startListener(t, nil)
	sendAnnouncement(t, []byte(`{"topic":"printer.status","version":1,"robotId":"x","address":"a:1"}`))
	sendAnnouncement(t, []byte(`not json at all`))
	sendAnnouncement(t, payloadFor(t, "xlerobot-0001", "192.168.50.73:50051", discovery.PairingPaired))

	waitFor(t, "the real robot", func() bool { return len(listener.Robots()) == 1 })
	waitFor(t, "the foreign traffic to be counted", func() bool { return listener.Dropped() >= 2 })
	if len(listener.Robots()) != 1 {
		t.Fatalf("robots = %d, want only the real one", len(listener.Robots()))
	}
}

// A robot speaking a newer protocol is reported as exactly that.
//
// This is the most actionable thing the listener can say: a robot is present and
// announcing, and the reason it is not in the list is a version difference. It
// must not be filed under the same counter as unrelated broadcast noise.
func TestARobotFromANewerVersionIsReportedAsAVersionMismatch(t *testing.T) {
	listener := startListener(t, nil)
	sendAnnouncement(t, []byte(
		`{"topic":"tangying.robot.announce","version":99,"robotId":"xlerobot-0002","address":"192.168.50.74:50051"}`))
	sendAnnouncement(t, []byte(`not json at all`))

	waitFor(t, "both packets to be classified", func() bool {
		return listener.Mismatched() == 1 && listener.Dropped() == 1
	})
	if len(listener.Robots()) != 0 {
		t.Fatal("a robot this build cannot read was listed as if it had been read")
	}
}

// A listener that cannot bind reports it rather than looking like an empty
// network.
func TestAPortThatCannotBeBoundIsReported(t *testing.T) {
	occupied, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4zero, Port: 0})
	if err != nil {
		t.Fatalf("occupy a port: %v", err)
	}
	defer occupied.Close()
	port := occupied.LocalAddr().(*net.UDPAddr).Port

	listener := discovery.NewListener()
	listener.Port = port
	err = listener.Listen(context.Background())
	if err == nil {
		t.Fatal("binding an occupied port reported success")
	}
	_ = listener.Stop()
}

// errorsIs avoids importing errors just for one comparison in a test file that
// otherwise needs nothing from it.
func errorsIs(err, target error) bool {
	for err != nil {
		if err == target {
			return true
		}
		unwrapper, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = unwrapper.Unwrap()
	}
	return false
}
