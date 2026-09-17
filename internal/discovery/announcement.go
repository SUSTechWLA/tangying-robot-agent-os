// Package discovery finds robots on the local network without configuration.
//
// This is the piece that turns "pair a robot" from a sequence of commands into
// "power it on". Until now the operator had to know the robot's hostname, have
// SSH access to it, and run a script from the laptop; the address ended up in a
// configuration file typed by hand. A robot that has just been unboxed and
// switched on announces itself instead, and the agent on the laptop listens.
//
// # Why a broadcast, and why plaintext
//
// The announcement is a UDP broadcast on a fixed port, and it is deliberately
// unauthenticated and unencrypted. It has to be: the whole point is that the two
// ends have no shared secret yet — establishing one is what pairing is for.
//
// That constraint decides what an announcement may contain. It is readable by
// anything on the network, so it carries nothing that is not already visible to
// anything on the network: a robot's identity, the address to reach it, the
// adapter it runs, and whether it has been paired. The pairing code is never
// announced. Announcing it would make the physical-presence requirement
// meaningless — anyone on the network could pair.
//
// # The contract with the robot side
//
// The robot runtime is Python and this listener is Go, so the wire format is the
// contract between them and it is pinned by a fixture both sides are tested
// against: `tests/contract/robot_announcement.json`. The Python side asserts that
// what it emits matches that file byte for byte; this side asserts that it can
// read that exact file. A change to either side that the other does not expect
// fails a test rather than failing silently in a user's living room.
package discovery

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"strings"
	"time"
)

// AnnouncementPort is the UDP port robots announce on and agents listen to.
//
// It is a fixed, high port rather than a service-name lookup because there is no
// name server involved and no configuration step permitted: the two sides agree
// here, in one constant, on both sides of the language boundary.
const AnnouncementPort = 45871

// AnnouncementGroup is the broadcast address used when a subnet broadcast cannot
// be determined. 255.255.255.255 reaches the local link on every platform this
// runs on, which is the only reach pairing needs.
const AnnouncementGroup = "255.255.255.255"

// ProtocolVersion is the version of this wire format.
//
// A reader refuses an announcement it does not understand rather than guessing at
// it. A robot running newer firmware must not be half-understood by an older
// agent: the fields that decide what to do with it may be exactly the ones that
// changed.
const ProtocolVersion = 1

// AnnouncementTopic identifies the payload. A stray UDP packet on this port is
// ignored, because a listener that acted on anything it received would treat
// unrelated broadcast traffic as a robot.
const AnnouncementTopic = "tangying.robot.announce"

// MaxAnnouncementSize bounds what a listener will read from one datagram.
//
// An announcement is a few hundred bytes. A datagram larger than this is either
// not ours or is hostile, and reading it into memory to find out would be the
// wrong order.
const MaxAnnouncementSize = 2048

// Announcement is what a robot says about itself, unauthenticated.
//
// Every field is one the robot already knows at startup. Nothing here is
// collected for the benefit of discovery: a robot that cannot announce still runs
// exactly as before, and an agent that never hears an announcement behaves
// exactly as it did before this package existed.
type Announcement struct {
	// Topic is always AnnouncementTopic. It is in the payload as well as being
	// implied by the port so a stray packet is identifiable on its own.
	Topic string `json:"topic"`
	// Version is ProtocolVersion.
	Version int `json:"version"`
	// RobotID is the robot's stable identity, the same one its runtime reports.
	// It is what makes two announcements from a moving address one robot.
	RobotID string `json:"robotId"`
	// Hostname is the name the robot believes it has. It is offered as a
	// convenience for display and for certificate subject names, not as an
	// authority: a name that does not resolve is still a robot at Address.
	Hostname string `json:"hostname"`
	// Address is host:port where the robot runtime listens.
	Address string `json:"address"`
	// Adapter names the hardware adapter the runtime is running, for example
	// "xlerobot". It tells an operator which robot they are looking at before
	// they pair with it.
	Adapter string `json:"adapter"`
	// PairingState is one of PairingUnpaired, PairingPaired or PairingOpen.
	//
	// It is a statement by the robot about itself, and it is unverified. The
	// agent must not treat it as authorisation to do anything: it decides how a
	// robot is displayed, and the actual decision about whether a pairing is
	// possible is made by the robot when the pairing request arrives.
	PairingState string `json:"pairingState"`
	// CapabilityCount is how many tools the runtime offers. It is a summary, not
	// the catalogue: the catalogue is the robot's to publish, and copying it
	// into a broadcast would make the broadcast the second source of truth for
	// what the robot can do.
	CapabilityCount int `json:"capabilityCount"`
	// EnrollmentPort is where a pairing request should be sent, and it is present
	// only while a window is open.
	//
	// The agent must not assume the default port. A deployment is free to run the
	// pairing listener elsewhere, and an agent that guessed would report "the
	// robot is not offering to be paired" while the robot was in fact waiting —
	// which is exactly what happened the first time this was run end to end.
	EnrollmentPort int `json:"enrollmentPort,omitempty"`
	// SentAt is when the announcement was made, by the robot's clock.
	SentAt time.Time `json:"sentAt"`
}

// Pairing states a robot may announce.
const (
	// PairingUnpaired means the robot has no server certificate and cannot
	// accept a mutually authenticated connection yet.
	PairingUnpaired = "unpaired"
	// PairingOpen means the robot is accepting a pairing request right now —
	// typically a short window after boot or after someone pressed its pairing
	// button. Finding a robot in this state is what makes it reasonable for a
	// console to offer "pair" as the next step.
	PairingOpen = "open"
	// PairingPaired means the robot already holds a certificate.
	PairingPaired = "paired"
)

// ErrNotAnnouncement is returned for a datagram that is not one of ours.
//
// It is a distinct error rather than a generic parse failure because the caller's
// response differs: an unparseable packet on a shared port is noise and is
// ignored, while a malformed announcement is a version or firmware problem worth
// reporting.
var ErrNotAnnouncement = errors.New("datagram is not a robot announcement")

// ErrUnsupportedVersion is returned for an announcement from another protocol
// version.
var ErrUnsupportedVersion = errors.New("unsupported announcement protocol version")

// Encode renders the announcement as the bytes that go on the wire.
//
// It exists on this side even though only the robot sends announcements, so that
// a test can produce exactly what the robot is supposed to produce and the two
// implementations are checked against one definition rather than two.
func (a Announcement) Encode() ([]byte, error) {
	if a.RobotID == "" {
		return nil, errors.New("announcement requires a robot id")
	}
	if a.Address == "" {
		return nil, errors.New("announcement requires an address")
	}
	encoded := a
	encoded.Topic = AnnouncementTopic
	encoded.Version = ProtocolVersion
	if encoded.SentAt.IsZero() {
		encoded.SentAt = time.Now().UTC()
	}
	return json.Marshal(encoded)
}

// DecodeAnnouncement parses one datagram.
//
// Fields that decide what the announcement *is* are required and their absence
// is an error: a robot with no identity or no address cannot be shown or
// contacted, and inventing a placeholder for either would put a robot in a list
// that cannot be acted on. Everything else is optional and degrades to a less
// informative but still true entry.
func DecodeAnnouncement(data []byte) (Announcement, error) {
	if len(data) == 0 || len(data) > MaxAnnouncementSize {
		return Announcement{}, ErrNotAnnouncement
	}
	var announcement Announcement
	if err := json.Unmarshal(data, &announcement); err != nil {
		return Announcement{}, ErrNotAnnouncement
	}
	if announcement.Topic != AnnouncementTopic {
		return Announcement{}, ErrNotAnnouncement
	}
	if announcement.Version != ProtocolVersion {
		return Announcement{}, fmt.Errorf("%w: got %d, want %d",
			ErrUnsupportedVersion, announcement.Version, ProtocolVersion)
	}
	if strings.TrimSpace(announcement.RobotID) == "" {
		return Announcement{}, fmt.Errorf("%w: no robot id", ErrNotAnnouncement)
	}
	if strings.TrimSpace(announcement.Address) == "" {
		return Announcement{}, fmt.Errorf("%w: robot %s announced no address",
			ErrNotAnnouncement, announcement.RobotID)
	}
	if announcement.PairingState == "" {
		// An unknown state is not treated as unpaired. Guessing "unpaired" would
		// make an agent offer to pair a robot that may already be paired, and
		// guessing "paired" would hide one that needs pairing.
		announcement.PairingState = PairingUnpaired
	}
	return announcement, nil
}

// Send broadcasts one announcement and returns when it has been written.
//
// It is used by the robot side and by tests. Broadcasting is done per-interface
// rather than once to a global address because a machine with several networks
// (a laptop on Wi-Fi and a cable, a robot with an access-point interface) would
// otherwise announce on whichever one the routing table prefers, which is
// frequently the wrong one for the person standing next to the robot.
func Send(announcement Announcement, port int) error {
	payload, err := announcement.Encode()
	if err != nil {
		return err
	}
	if port <= 0 {
		port = AnnouncementPort
	}
	connections, err := broadcastTargets(port)
	if err != nil {
		return err
	}
	defer func() {
		for _, connection := range connections {
			_ = connection.Close()
		}
	}()
	if len(connections) == 0 {
		return errors.New("no broadcast-capable interface found")
	}
	var firstError error
	sent := 0
	for _, connection := range connections {
		if _, err := connection.Write(payload); err != nil {
			if firstError == nil {
				firstError = err
			}
			continue
		}
		sent++
	}
	if sent == 0 {
		return fmt.Errorf("announcement was not sent on any interface: %w", firstError)
	}
	return nil
}

// broadcastTargets opens one UDP socket per broadcast-capable interface, each
// aimed at that interface's own broadcast address.
func broadcastTargets(port int) ([]*net.UDPConn, error) {
	interfaces, err := net.Interfaces()
	if err != nil {
		return nil, err
	}
	connections := make([]*net.UDPConn, 0, len(interfaces))
	for _, iface := range interfaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagBroadcast == 0 {
			continue
		}
		// A loopback interface is not how a robot is reached, and announcing on
		// it would put the robot in the agent's own discovery list when both run
		// on one machine — which is how the simulator is deployed. That entry is
		// real but it is reached by its configured address, not by discovery.
		if iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addresses, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, address := range addresses {
			ipNet, ok := address.(*net.IPNet)
			if !ok {
				continue
			}
			ip := ipNet.IP.To4()
			if ip == nil {
				continue
			}
			broadcast := net.IPv4(
				ip[0]|^ipNet.Mask[0], ip[1]|^ipNet.Mask[1],
				ip[2]|^ipNet.Mask[2], ip[3]|^ipNet.Mask[3],
			)
			connection, err := net.DialUDP("udp4", nil, &net.UDPAddr{IP: broadcast, Port: port})
			if err != nil {
				continue
			}
			connections = append(connections, connection)
		}
	}
	return connections, nil
}
