package pairing

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"time"
)

// Client pairs with a robot over the network, with no SSH anywhere.
//
// This is the whole point of the package: `scripts/pair-robot.sh` needs a
// hostname, an SSH account, key-based authentication and `sudo` on the far side.
// An owner has none of those. What they have is the robot on the same network and
// a code the robot printed.
//
// The client never sends anything before the robot has proved it knows the code.
// Establishing that is the first exchange, and the certificate material travels
// sealed under a key derived from the code — so an eavesdropper learns nothing and
// an impostor cannot answer.
type Client struct {
	// Timeout bounds one attempt. Pairing is a local network act; a minute is
	// generous and a hang is worse than a failure, because a hang has no message.
	Timeout time.Duration
	// Port is the robot's enrollment port. Zero means EnrollPort.
	Port int
	// Dial, when set, replaces the network dial. Tests use it; production does not.
	Dial func(ctx context.Context, network, address string) (net.Conn, error)
}

// PairResult is what happened.
type PairResult struct {
	// RobotID is the robot that accepted the material.
	RobotID string
	// Address is what was dialled, for the record.
	Address string
	// Material is what was installed. The private key is included because the
	// caller writes the agent's own copy of the record; it is never logged.
	Material Material
}

// ErrUnreachable means the robot's enrollment port did not answer.
var ErrUnreachable = errors.New("the robot did not answer on its pairing port")

// Pair joins one robot.
//
// Every failure is returned with a sentence an operator can act on, because the
// person running this is not going to read the source. "Connection refused" tells
// an owner nothing; "the robot is not offering to be paired right now, and the
// window closes by itself" tells them what to do next.
func (c *Client) Pair(ctx context.Context, code string, announcement Address) (PairResult, error) {
	if NormalizeCode(code) == "" {
		return PairResult{}, errors.New("a pairing code is required; the robot prints it at startup")
	}
	if announcement.RobotID == "" || announcement.Host == "" {
		return PairResult{}, errors.New("pairing needs a robot identity and an address")
	}
	timeout := c.Timeout
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	port := c.Port
	if port <= 0 {
		port = EnrollPort
	}
	address := net.JoinHostPort(announcement.Host, fmt.Sprintf("%d", port))

	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	dial := c.Dial
	if dial == nil {
		dialer := &net.Dialer{}
		dial = dialer.DialContext
	}
	connection, err := dial(ctx, "tcp", address)
	if err != nil {
		return PairResult{}, fmt.Errorf(
			"%w at %s: 机器人可能已经配对、配对窗口已经关闭，或者它不在这个网络上", ErrUnreachable, address)
	}
	defer connection.Close()
	if deadline, ok := ctx.Deadline(); ok {
		_ = connection.SetDeadline(deadline)
	}

	// Issued here rather than passed in, so the caller cannot accidentally send a
	// certificate for the wrong robot: the names come from the address being
	// dialled.
	material, err := announcement.Authority.RobotMaterial(announcement.Host, announcement.IP)
	if err != nil {
		return PairResult{}, err
	}
	request, err := EncodeRequest(code, material, announcement.RobotID)
	if err != nil {
		return PairResult{}, err
	}
	if err := WriteFrame(connection, request); err != nil {
		return PairResult{}, fmt.Errorf("send the pairing request: %w", err)
	}

	answer, err := ReadFrame(connection)
	if err != nil {
		// The robot closes the connection without answering when it refuses — a
		// wrong code is not told apart from a tampered message, because AES-GCM
		// cannot tell them apart and inventing a distinction would be inventing
		// information the cryptography did not provide.
		return PairResult{}, fmt.Errorf(
			"机器人拒绝了这次配对：配对码不正确（每个码只能用一次，用错 5 次窗口会自动关闭）")
	}
	salt, err := saltOf(request)
	if err != nil {
		return PairResult{}, err
	}
	body, err := DecodeResult(code, salt, announcement.RobotID, answer)
	if err != nil {
		return PairResult{}, err
	}
	if body.Status != "paired" {
		detail := body.Detail
		if detail == "" {
			detail = "机器人没有说明原因"
		}
		return PairResult{}, fmt.Errorf("%w: %s", ErrRobotRefused, detail)
	}
	return PairResult{RobotID: announcement.RobotID, Address: address, Material: material}, nil
}

// Address is what the agent knows about a robot before pairing it: where it is,
// what it calls itself, and which authority will sign for it.
type Address struct {
	RobotID string
	Host    string
	IP      string
	// Authority signs the robot's certificate. It is required: a pairing that
	// issued no certificate would produce a robot the agent still cannot verify.
	Authority *Authority
}

// saltOf reads back the salt this side generated, which the answer is sealed
// under.
//
// It is parsed from the request that was actually sent rather than carried
// alongside it, so the value used to open the answer is provably the value the
// robot received. Keeping a second copy is how the two come to differ.
func saltOf(request []byte) ([]byte, error) {
	var envelope Request
	if err := json.Unmarshal(request, &envelope); err != nil {
		return nil, fmt.Errorf("re-read the request just sent: %w", err)
	}
	salt, err := base64.StdEncoding.DecodeString(envelope.Salt)
	if err != nil {
		return nil, fmt.Errorf("re-read the salt just sent: %w", err)
	}
	return salt, nil
}
