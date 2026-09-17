// Package pairing joins a robot to an agent without SSH.
//
// # Why this exists
//
// Discovery makes a robot findable; this makes it usable. Until now the step
// between the two was `scripts/pair-robot.sh`, which needs the robot's hostname,
// an SSH account, key-based authentication and `sudo` on the far side. That is a
// technician's workflow, not an owner's, and it is the last thing standing between
// "power it on" and "talk to it".
//
// # The problem that shapes the design
//
// The two ends share no secret, and you cannot authenticate a device you share no
// secret with. Everything below follows from taking that seriously instead of
// waving at it:
//
//   - The robot prints a one-time pairing code. It is the only pre-shared value,
//     and it is short enough for a person to read off a label or a log and type.
//   - The code is a pre-shared key. The agent proves it knows the code, the robot
//     proves it knows the code, and the certificate material the agent sends is
//     encrypted under a key derived from it — so an eavesdropper on the network
//     learns nothing, and an impostor cannot answer.
//   - The code is single-use and expires. A pairing code that survives its own
//     pairing is a permanent key to the robot.
//
// # What is deliberately not done here
//
// No custom cryptographic primitive is invented. HKDF-SHA256 and AES-256-GCM are
// used as specified, and the HKDF implementation is checked against the RFC 5869
// test vectors and against Python's implementation of the same standard, because
// hand-written key derivation is exactly the kind of code that is wrong in a way
// nothing notices.
//
// # The threat model, stated plainly
//
//   - A passive eavesdropper learns nothing: the payload is encrypted and the
//     handshake reveals no plaintext.
//   - An active impostor cannot answer, because it does not know the code.
//   - Someone who *does* know the code can pair, and can therefore install a
//     certificate. That is the intended meaning of the code: it is the thing that
//     says "I am standing at this robot". Physical possession is the root of trust,
//     which is why the code is printed by the robot and never broadcast.
//   - An attacker who can read the code and race the owner wins. The mitigations
//     are the ones that fit the threat: the code is single-use, it expires, and
//     the robot reports every attempt.
package pairing

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
)

// Protocol constants. They are the contract with the robot, which is Python, and
// they are pinned by a fixture both sides are tested against.
const (
	// EnrollTopic identifies an enrollment request.
	EnrollTopic = "tangying.robot.enroll"
	// ResultTopic identifies the robot's answer.
	ResultTopic = "tangying.robot.enroll.result"
	// EnrollVersion is the version of this wire format.
	EnrollVersion = 1
	// EnrollPort is the TCP port an unpaired robot listens on.
	//
	// It sits next to the announcement port so the two are remembered together:
	// a robot is found on 45871 and joined on 45872.
	EnrollPort = 45872
	// MaxEnrollMessage bounds one framed message.
	//
	// A request carries a CA certificate, a leaf certificate and a private key —
	// a few kilobytes. A message larger than this is not one of ours, and reading
	// it into memory to find out would be the wrong order.
	MaxEnrollMessage = 64 * 1024
	// keyInfo is the HKDF info string. It separates this key from any other key
	// derived from the same code, so a future protocol cannot accidentally reuse
	// this one's key stream.
	keyInfo = "tangying.robot.enroll.v1"
	// keyLength is the AES-256 key length.
	keyLength = 32
)

// Errors callers act on.
var (
	// ErrWrongCode means the robot could not decrypt the request: the code is
	// wrong, or the request was not made by someone holding it.
	ErrWrongCode = errors.New("pairing code is wrong")
	// ErrRobotRefused means the robot understood the request and declined it.
	ErrRobotRefused = errors.New("robot refused the pairing")
	// ErrNotEnrollment means the message was not an enrollment message.
	ErrNotEnrollment = errors.New("message is not an enrollment message")
	// ErrUnsupportedVersion means the other end speaks a different protocol.
	ErrUnsupportedVersion = errors.New("unsupported enrollment protocol version")
)

// Material is what the agent installs on the robot.
//
// It is the same three files `scripts/pair-robot.sh` deploys over SSH, which is
// the point: this is a different delivery mechanism for one set of artefacts, not
// a second kind of pairing. A robot paired either way ends up byte-identical.
type Material struct {
	// CA is the agent's CA certificate in PEM. The robot uses it to verify the
	// agent's client certificate, so it is the half that makes mutual TLS mutual.
	CA string `json:"ca"`
	// ServerCert is the robot's own certificate, signed by that CA.
	ServerCert string `json:"serverCert"`
	// ServerKey is the robot's private key.
	ServerKey string `json:"serverKey"`
}

// Request is what the agent sends.
type Request struct {
	Topic   string `json:"topic"`
	Version int    `json:"version"`
	RobotID string `json:"robotId"`
	// Salt is fresh per attempt, from the agent.
	Salt string `json:"salt"`
	// Nonce is the AES-GCM nonce, fresh per attempt.
	Nonce string `json:"nonce"`
	// Payload is the sealed Material.
	Payload string `json:"payload"`
}

// Result is the robot's answer.
type Result struct {
	Topic   string `json:"topic"`
	Version int    `json:"version"`
	RobotID string `json:"robotId"`
	Nonce   string `json:"nonce"`
	Payload string `json:"payload"`
}

// ResultBody is the decrypted answer.
type ResultBody struct {
	// Status is "paired" or "refused".
	Status string `json:"status"`
	// Detail explains a refusal, in a sentence meant for the operator.
	Detail string `json:"detail,omitempty"`
}

// NormalizeCode puts a pairing code into the one form both ends hash.
//
// The code is read off a label or a log by a person and typed, so the same code
// arrives as "4F2K-9QW7" or "4f2k9qw7" or with a space in it. Normalising here
// rather than at the comparison means a correct code is never refused for
// looking different, and it is done identically on both sides because the two
// implementations are checked against one fixture.
func NormalizeCode(code string) string {
	var builder strings.Builder
	for _, symbol := range strings.ToUpper(strings.TrimSpace(code)) {
		if symbol == '-' || symbol == ' ' || symbol == '_' || symbol == '\t' {
			continue
		}
		builder.WriteRune(symbol)
	}
	return builder.String()
}

// DeriveKey turns a pairing code into the AES key both ends use.
//
// The salt is fresh per attempt from the agent, so two pairings of the same robot
// never derive the same key, and a recorded attempt cannot be replayed later.
func DeriveKey(code string, salt []byte) ([]byte, error) {
	normalized := NormalizeCode(code)
	if normalized == "" {
		return nil, ErrWrongCode
	}
	// The code is the input keying material. It is short by design — a person has
	// to read it — so it is used through HKDF rather than directly, which is what
	// HKDF is for: it turns a low-entropy shared secret into a uniformly random
	// key without pretending the secret had more entropy than it did.
	return hkdfSHA256([]byte(normalized), salt, []byte(keyInfo), keyLength), nil
}

// Seal encrypts one payload under a code.
//
// The additional data binds the ciphertext to a robot. Without it, a request
// captured while pairing robot A could be replayed at robot B by anyone who had
// also learned the code for B — and "the code is per robot" would be a claim the
// cryptography did not actually make.
func Seal(code string, salt, nonce []byte, robotID string, plaintext []byte) (string, error) {
	key, err := DeriveKey(code, salt)
	if err != nil {
		return "", err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", fmt.Errorf("cipher: %w", err)
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return "", fmt.Errorf("gcm: %w", err)
	}
	if len(nonce) != aead.NonceSize() {
		return "", fmt.Errorf("nonce must be %d bytes, got %d", aead.NonceSize(), len(nonce))
	}
	sealed := aead.Seal(nil, nonce, plaintext, []byte(robotID))
	return base64.StdEncoding.EncodeToString(sealed), nil
}

// Open decrypts one payload, returning ErrWrongCode when the code does not match.
//
// A wrong code and a tampered message are the same answer on purpose. AES-GCM
// cannot tell them apart, and inventing a distinction would be inventing
// information the cryptography did not provide.
func Open(code string, salt, nonce []byte, robotID, payload string) ([]byte, error) {
	key, err := DeriveKey(code, salt)
	if err != nil {
		return nil, err
	}
	sealed, err := base64.StdEncoding.DecodeString(payload)
	if err != nil {
		return nil, ErrWrongCode
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, fmt.Errorf("cipher: %w", err)
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, fmt.Errorf("gcm: %w", err)
	}
	if len(nonce) != aead.NonceSize() {
		return nil, fmt.Errorf("nonce must be %d bytes, got %d", aead.NonceSize(), len(nonce))
	}
	opened, err := aead.Open(nil, nonce, sealed, []byte(robotID))
	if err != nil {
		return nil, ErrWrongCode
	}
	return opened, nil
}

// NewSalt and NewNonce produce the fresh values one attempt needs.
func NewSalt() ([]byte, error) { return randomBytes(32) }
func NewNonce() ([]byte, error) {
	return randomBytes(12) // AES-GCM's standard nonce size
}

func randomBytes(size int) ([]byte, error) {
	buffer := make([]byte, size)
	if _, err := rand.Read(buffer); err != nil {
		return nil, fmt.Errorf("random: %w", err)
	}
	return buffer, nil
}

// EncodeRequest builds the sealed request for one pairing attempt.
func EncodeRequest(code string, material Material, robotID string) ([]byte, error) {
	salt, err := NewSalt()
	if err != nil {
		return nil, err
	}
	nonce, err := NewNonce()
	if err != nil {
		return nil, err
	}
	plaintext, err := json.Marshal(material)
	if err != nil {
		return nil, fmt.Errorf("encode material: %w", err)
	}
	sealed, err := Seal(code, salt, nonce, robotID, plaintext)
	if err != nil {
		return nil, err
	}
	return json.Marshal(Request{
		Topic: EnrollTopic, Version: EnrollVersion, RobotID: robotID,
		Salt:    base64.StdEncoding.EncodeToString(salt),
		Nonce:   base64.StdEncoding.EncodeToString(nonce),
		Payload: sealed,
	})
}

// DecodeRequest reads a request and decrypts its material.
func DecodeRequest(code string, data []byte) (Material, Request, error) {
	var request Request
	if len(data) == 0 || len(data) > MaxEnrollMessage {
		return Material{}, Request{}, ErrNotEnrollment
	}
	if err := json.Unmarshal(data, &request); err != nil {
		return Material{}, Request{}, ErrNotEnrollment
	}
	if request.Topic != EnrollTopic {
		return Material{}, Request{}, ErrNotEnrollment
	}
	if request.Version != EnrollVersion {
		return Material{}, Request{}, fmt.Errorf("%w: got %d, want %d",
			ErrUnsupportedVersion, request.Version, EnrollVersion)
	}
	if strings.TrimSpace(request.RobotID) == "" {
		return Material{}, Request{}, fmt.Errorf("%w: no robot id", ErrNotEnrollment)
	}
	salt, err := base64.StdEncoding.DecodeString(request.Salt)
	if err != nil {
		return Material{}, Request{}, ErrWrongCode
	}
	nonce, err := base64.StdEncoding.DecodeString(request.Nonce)
	if err != nil {
		return Material{}, Request{}, ErrWrongCode
	}
	plaintext, err := Open(code, salt, nonce, request.RobotID, request.Payload)
	if err != nil {
		return Material{}, request, err
	}
	var material Material
	if err := json.Unmarshal(plaintext, &material); err != nil {
		return Material{}, request, fmt.Errorf("%w: material did not decode", ErrNotEnrollment)
	}
	if strings.TrimSpace(material.CA) == "" || strings.TrimSpace(material.ServerCert) == "" ||
		strings.TrimSpace(material.ServerKey) == "" {
		return Material{}, request, fmt.Errorf("%w: incomplete material", ErrNotEnrollment)
	}
	return material, request, nil
}

// EncodeResult builds the robot's sealed answer.
func EncodeResult(code string, salt, nonce []byte, robotID string, body ResultBody) ([]byte, error) {
	plaintext, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("encode result: %w", err)
	}
	sealed, err := Seal(code, salt, nonce, robotID, plaintext)
	if err != nil {
		return nil, err
	}
	return json.Marshal(Result{
		Topic: ResultTopic, Version: EnrollVersion, RobotID: robotID,
		Nonce:   base64.StdEncoding.EncodeToString(nonce),
		Payload: sealed,
	})
}

// DecodeResult reads the robot's answer.
//
// The answer is sealed under the same code, which is what makes it an
// authentication and not just a reply: only something that knows the code can
// produce a ciphertext this side will accept.
func DecodeResult(code string, salt []byte, robotID string, data []byte) (ResultBody, error) {
	var result Result
	if len(data) == 0 || len(data) > MaxEnrollMessage {
		return ResultBody{}, ErrNotEnrollment
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return ResultBody{}, ErrNotEnrollment
	}
	if result.Topic != ResultTopic {
		return ResultBody{}, ErrNotEnrollment
	}
	if result.Version != EnrollVersion {
		return ResultBody{}, fmt.Errorf("%w: got %d, want %d",
			ErrUnsupportedVersion, result.Version, EnrollVersion)
	}
	nonce, err := base64.StdEncoding.DecodeString(result.Nonce)
	if err != nil {
		return ResultBody{}, ErrNotEnrollment
	}
	plaintext, err := Open(code, salt, nonce, robotID, result.Payload)
	if err != nil {
		// Not knowing the code is not the failure here: the agent does know it.
		// A message that will not open came from something that does not.
		return ResultBody{}, fmt.Errorf("%w: the answer was not sealed with this code", ErrNotEnrollment)
	}
	var body ResultBody
	if err := json.Unmarshal(plaintext, &body); err != nil {
		return ResultBody{}, ErrNotEnrollment
	}
	return body, nil
}

// WriteFrame writes one length-prefixed message.
//
// A length prefix rather than a stream terminator, because the payload is base64
// JSON that may legitimately contain any byte a terminator would use.
func WriteFrame(writer io.Writer, message []byte) error {
	if len(message) > MaxEnrollMessage {
		return fmt.Errorf("message is %d bytes, limit is %d", len(message), MaxEnrollMessage)
	}
	var header [4]byte
	binary.BigEndian.PutUint32(header[:], uint32(len(message)))
	if _, err := writer.Write(header[:]); err != nil {
		return fmt.Errorf("write frame header: %w", err)
	}
	if _, err := writer.Write(message); err != nil {
		return fmt.Errorf("write frame body: %w", err)
	}
	return nil
}

// ReadFrame reads one length-prefixed message.
func ReadFrame(reader io.Reader) ([]byte, error) {
	var header [4]byte
	if _, err := io.ReadFull(reader, header[:]); err != nil {
		return nil, err
	}
	size := binary.BigEndian.Uint32(header[:])
	if size == 0 || size > MaxEnrollMessage {
		return nil, fmt.Errorf("framed message of %d bytes is out of range", size)
	}
	body := make([]byte, size)
	if _, err := io.ReadFull(reader, body); err != nil {
		return nil, err
	}
	return body, nil
}

// EqualCodes compares two pairing codes without leaking where they differ.
//
// A byte-by-byte comparison that stops at the first difference tells an attacker
// how much of a guess was right, which turns guessing a short code into a
// sequence of cheap questions. The comparison is constant time for that reason,
// and it compares the normalised forms so normalisation cannot be used as an
// oracle either.
func EqualCodes(left, right string) bool {
	normalizedLeft := NormalizeCode(left)
	normalizedRight := NormalizeCode(right)
	// An empty code matches nothing, including another empty code.
	//
	// constant-time comparison alone would answer "equal" for two empty strings,
	// which means a robot with no code configured would accept a request carrying
	// no code. "No code" must mean "no pairing is possible", never "any pairing".
	if normalizedLeft == "" || normalizedRight == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(normalizedLeft), []byte(normalizedRight)) == 1
}

// hkdfSHA256 is HKDF as specified by RFC 5869, using SHA-256.
//
// It is written out rather than imported because `golang.org/x/crypto` is not
// vendored in this repository and adding a dependency to a vendored tree is a
// larger change than this needs. Hand-written key derivation is exactly the code
// that is wrong in a way nothing notices, so it is checked against the RFC's own
// test vectors and against Python's implementation of the same standard — see
// hkdf_test.go and the cross-language fixture.
func hkdfSHA256(secret, salt, info []byte, length int) []byte {
	if length <= 0 || length > 255*sha256.Size {
		panic("hkdf: invalid output length")
	}
	if len(salt) == 0 {
		// RFC 5869: a zero-length salt is a string of HashLen zeros.
		salt = make([]byte, sha256.Size)
	}
	// Extract.
	extractor := hmac.New(sha256.New, salt)
	extractor.Write(secret)
	prk := extractor.Sum(nil)

	// Expand.
	output := make([]byte, 0, length)
	var previous []byte
	for counter := byte(1); len(output) < length; counter++ {
		expander := hmac.New(sha256.New, prk)
		expander.Write(previous)
		expander.Write(info)
		expander.Write([]byte{counter})
		previous = expander.Sum(nil)
		output = append(output, previous...)
	}
	return output[:length]
}
