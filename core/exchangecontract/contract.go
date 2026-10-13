// Package exchangecontract defines advisory data exchange. An envelope is never
// an execution grant, command, lease, or replacement for local authoritative state.
package exchangecontract

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
)

const Schema = "agent.data.v1"
const MaxPayload = 64 * 1024
const MaxTTLMS int64 = 24 * 60 * 60 * 1000
const FutureSkewMS int64 = 30 * 1000

// Expired bodies and publication request IDs are retained for one further day.
// Stream cursors and database identity are never garbage collected.
const RetentionMS int64 = MaxTTLMS

var (
	ErrConflict = errors.New("exchange identity or sequence conflict")
	ErrStale    = errors.New("exchange sequence is stale")
	ErrExpired  = errors.New("exchange data expired")
	ErrCapacity = errors.New("exchange journal capacity reached")
	identifier  = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$`)
)

type Identity struct {
	AgentID string `json:"agentId"`
	RobotID string `json:"robotId"`
	Domain  string `json:"domain"`
}

func (i Identity) Validate() error {
	if !identifier.MatchString(i.AgentID) || !identifier.MatchString(i.RobotID) || !identifier.MatchString(i.Domain) {
		return errors.New("agent, robot and domain identifiers are required and must be bounded")
	}
	return nil
}

// Basis is mandatory for spatial observations/maps. Receiving this metadata
// does not assert that two robots' coordinate systems are interchangeable.
type Basis struct {
	WorldID           string `json:"worldId"`
	FrameID           string `json:"frameId"`
	TransformRevision string `json:"transformRevision"`
	MapID             string `json:"mapId,omitempty"`
	MapRevision       string `json:"mapRevision,omitempty"`
}

type Envelope struct {
	SchemaVersion string          `json:"schemaVersion"`
	MessageID     string          `json:"messageId"`
	Sender        Identity        `json:"sender"`
	Recipient     Identity        `json:"recipient"`
	Stream        string          `json:"stream"`
	Kind          string          `json:"kind"`
	Sequence      uint64          `json:"sequence,string"`
	CreatedAtMS   int64           `json:"createdAtMs"`
	ExpiresAtMS   int64           `json:"expiresAtMs"`
	Basis         *Basis          `json:"basis,omitempty"`
	Payload       json.RawMessage `json:"payload"`
}

type Request struct {
	RequestID string          `json:"requestId"`
	PeerID    string          `json:"peerId"`
	Stream    string          `json:"stream"`
	Kind      string          `json:"kind"`
	TTLMS     int64           `json:"ttlMs"`
	Basis     *Basis          `json:"basis,omitempty"`
	Payload   json.RawMessage `json:"payload"`
}

type Receipt struct {
	SchemaVersion string `json:"schemaVersion"`
	MessageID     string `json:"messageId"`
	Sequence      uint64 `json:"sequence,string"`
	Disposition   string `json:"disposition"` // accepted or duplicate, both durable
}

type Counts struct {
	Inbox        int   `json:"inbox"`
	Pending      int   `json:"pending"`
	Delivered    int   `json:"delivered"`
	Expired      int   `json:"expired"`
	PrunedInbox  int64 `json:"prunedInbox"`
	PrunedOutbox int64 `json:"prunedOutbox"`
}

// Journal atomically allocates outgoing sequences and stores incoming data with
// its cursor. Implementations must durably bind the database to Identity.
type Journal interface {
	Bind(context.Context, Identity) error
	Publish(context.Context, string, string, Envelope, int64) (Envelope, error)
	Accept(context.Context, Envelope, int64) (Receipt, error)
	Pending(context.Context, int64, int) ([]Envelope, error)
	Acknowledge(context.Context, Envelope, int64) error
	Inbox(context.Context, int) ([]Envelope, error)
	Counts(context.Context, int64) (Counts, error)
	Prune(context.Context, int64) error
}

func ValidIdentifier(value string) bool { return identifier.MatchString(value) }

func CanonicalPayload(raw json.RawMessage) (json.RawMessage, error) {
	if len(raw) == 0 || len(raw) > MaxPayload {
		return nil, errors.New("payload must contain at most 65536 bytes")
	}
	if err := ValidateJSON(raw); err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var payload map[string]any
	if err := decoder.Decode(&payload); err != nil || payload == nil {
		return nil, errors.New("payload must be a JSON object")
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return nil, errors.New("payload contains trailing JSON")
	}
	result, err := json.Marshal(payload)
	if len(result) > MaxPayload {
		return nil, errors.New("canonical payload exceeds limit")
	}
	return result, err
}

// ValidateJSON rejects duplicate keys and excessive nesting before Go's decoder
// could silently choose a value. It applies to the entire HTTP envelope too.
func ValidateJSON(raw []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var walk func(int) error
	walk = func(depth int) error {
		if depth > 32 {
			return errors.New("JSON nesting exceeds 32")
		}
		token, err := decoder.Token()
		if err != nil {
			return err
		}
		delim, ok := token.(json.Delim)
		if !ok {
			return nil
		}
		switch delim {
		case '{':
			keys := map[string]bool{}
			for decoder.More() {
				key, err := decoder.Token()
				if err != nil {
					return err
				}
				name, ok := key.(string)
				if !ok {
					return errors.New("invalid JSON object key")
				}
				if keys[name] {
					return errors.New("duplicate JSON object key")
				}
				keys[name] = true
				if err := walk(depth + 1); err != nil {
					return err
				}
			}
		case '[':
			for decoder.More() {
				if err := walk(depth + 1); err != nil {
					return err
				}
			}
		default:
			return errors.New("unexpected JSON delimiter")
		}
		_, err = decoder.Token()
		return err
	}
	if err := walk(0); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return errors.New("trailing JSON")
	}
	return nil
}

// Digest uses lexicographically sorted JSON object keys, compact JSON, UTF-8,
// Go encoding/json escaping, and sequence as a decimal string. MessageID is
// excluded. It is a content checksum, not authentication or a signature.
func (e Envelope) Digest() (string, error) {
	payload, err := CanonicalPayload(e.Payload)
	if err != nil {
		return "", err
	}
	e.Payload = payload
	e.MessageID = ""
	raw, err := json.Marshal(e)
	if err != nil {
		return "", err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var fields map[string]any
	if err := decoder.Decode(&fields); err != nil {
		return "", err
	}
	delete(fields, "messageId")
	canonical, err := json.Marshal(fields)
	if err != nil {
		return "", err
	}
	hash := sha256.Sum256(canonical)
	return "sha256:" + hex.EncodeToString(hash[:]), nil
}

func (e Envelope) Validate() error {
	if e.SchemaVersion != Schema {
		return errors.New("unsupported exchange schema")
	}
	if err := e.Sender.Validate(); err != nil {
		return err
	}
	if err := e.Recipient.Validate(); err != nil {
		return err
	}
	if e.Sender.AgentID == e.Recipient.AgentID {
		return errors.New("exchange requires different agents")
	}
	if !ValidIdentifier(e.Stream) || e.Sequence == 0 || e.Sequence > 1<<63-1 {
		return errors.New("invalid stream or sequence")
	}
	switch e.Kind {
	case "mission_status", "evidence", "observation", "map_reference":
	default:
		return errors.New("only advisory data kinds are allowed")
	}
	if e.CreatedAtMS <= 0 || e.CreatedAtMS > 1<<53-1 || e.ExpiresAtMS <= e.CreatedAtMS || e.ExpiresAtMS > 1<<53-1 || e.ExpiresAtMS-e.CreatedAtMS > MaxTTLMS {
		return errors.New("invalid or excessive data lifetime")
	}
	if (e.Kind == "observation" || e.Kind == "map_reference") && e.Basis == nil {
		return errors.New("spatial data requires explicit coordinate basis")
	}
	if b := e.Basis; b != nil {
		if !ValidIdentifier(b.WorldID) || !ValidIdentifier(b.FrameID) || !ValidIdentifier(b.TransformRevision) {
			return errors.New("invalid spatial basis")
		}
		if (b.MapID == "") != (b.MapRevision == "") || (b.MapID != "" && (!ValidIdentifier(b.MapID) || !ValidIdentifier(b.MapRevision))) {
			return errors.New("map identity and revision must be paired")
		}
		if e.Kind == "map_reference" && b.MapID == "" {
			return errors.New("map reference requires map identity and revision")
		}
	}
	digest, err := e.Digest()
	if err != nil {
		return err
	}
	if digest != e.MessageID {
		return fmt.Errorf("%w: message checksum", ErrConflict)
	}
	return nil
}

func (e Envelope) Fresh(nowMS int64) error {
	if e.ExpiresAtMS <= nowMS {
		return ErrExpired
	}
	if e.CreatedAtMS > nowMS+FutureSkewMS {
		return errors.New("sender clock exceeds allowed skew")
	}
	return nil
}
