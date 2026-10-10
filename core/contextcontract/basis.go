// Package contextcontract defines immutable cloud-to-edge execution context.
// A digest detects mixed/corrupt versions; it is not a signature or permission.
// Authentication, live claim renewal and physical safety remain separate gates.
package contextcontract

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"reflect"
	"strconv"
)

const SchemaVersion = "execution.context.v1"
const IntentScope = "fleet.intent"

var ErrInvalid = errors.New("invalid execution context basis")

type WorldBasis struct {
	Mode           string `json:"mode"` // coordinator_world or edge_local; never an implied global snapshot
	WorldID        string `json:"worldId,omitempty"`
	Revision       uint64 `json:"revision,omitempty"`
	EntitySourceID string `json:"entitySourceId,omitempty"`
	EntitySequence uint64 `json:"entitySequence,omitempty"`
	EntityCount    uint64 `json:"entityCount,omitempty"`
	RobotSourceID  string `json:"robotSourceId,omitempty"`
	RobotSequence  uint64 `json:"robotSequence,omitempty"`
}

type Basis struct {
	SchemaVersion    string     `json:"schemaVersion"`
	Scope            string     `json:"scope"`
	TaskID           string     `json:"taskId"`
	TaskRevision     uint64     `json:"taskRevision"`
	AggregateVersion uint64     `json:"aggregateVersion"`
	ClaimVersion     uint64     `json:"claimVersion"`
	IntentIndex      int        `json:"intentIndex"`
	StepID           string     `json:"stepId"`
	CommandID        string     `json:"commandId"`
	RobotID          string     `json:"robotId"`
	Adapter          string     `json:"adapter"`
	ExecutionDigest  string     `json:"executionDigest"`
	ResourceID       string     `json:"resourceId,omitempty"`
	FencingToken     uint64     `json:"fencingToken,omitempty"`
	CatalogRevision  string     `json:"catalogRevision,omitempty"`
	World            WorldBasis `json:"world"`
	Digest           string     `json:"digest"`
}

func CommandID(taskID string, revision uint64, stepID string) string {
	return fmt.Sprintf("%s/revision/%d/step/%s", taskID, revision, stepID)
}

func Fingerprint(value any) (string, error) {
	wire, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	decoder := json.NewDecoder(bytes.NewReader(wire))
	decoder.UseNumber()
	var decoded any
	if err := decoder.Decode(&decoded); err != nil {
		return "", err
	}
	wire, err = json.Marshal(canonicalNumbers(decoded))
	if err != nil {
		return "", err
	}
	hash := sha256.Sum256(wire)
	return hex.EncodeToString(hash[:]), nil
}

// Normalize portable decimal spellings (1e3 versus 1000) without converting
// typed large integers through float64. Argument rejection is a separate gate.
func canonicalNumbers(value any) any {
	switch v := value.(type) {
	case json.Number:
		n, err := v.Float64()
		if err != nil || math.IsInf(n, 0) || math.Abs(n) > 9007199254740991 {
			return v
		}
		short := strconv.FormatFloat(n, 'g', -1, 64)
		original, ok := new(big.Rat).SetString(v.String())
		canonical, shortOK := new(big.Rat).SetString(short)
		if ok && shortOK && original.Cmp(canonical) == 0 {
			return json.Number(short)
		}
	case map[string]any:
		for key, item := range v {
			v[key] = canonicalNumbers(item)
		}
	case []any:
		for i, item := range v {
			v[i] = canonicalNumbers(item)
		}
	}
	return value
}

// ValidatePortableArguments checks only values crossing an any-valued JSON
// boundary. Typed protocol integers (including UnixNano fencing tokens) retain
// their uint64 identity through typed HTTP/SQLite decoding and are not floats.
func ValidatePortableArguments(value any) error {
	return validateDynamicNumber(reflect.ValueOf(value), false, 0)
}

func validateDynamicNumber(v reflect.Value, dynamic bool, depth int) error {
	if depth > 100 {
		return fmt.Errorf("%w: context nesting too deep", ErrInvalid)
	}
	if !v.IsValid() {
		return nil
	}
	if v.Kind() == reflect.Interface {
		if v.IsNil() {
			return nil
		}
		return validateDynamicNumber(v.Elem(), true, depth+1)
	}
	if v.Kind() == reflect.Pointer {
		if v.IsNil() {
			return nil
		}
		return validateDynamicNumber(v.Elem(), dynamic, depth+1)
	}
	if v.CanInterface() {
		if raw, ok := v.Interface().(json.RawMessage); ok {
			if len(raw) == 0 {
				return nil
			}
			decoder := json.NewDecoder(bytes.NewReader(raw))
			decoder.UseNumber()
			var decoded any
			if err := decoder.Decode(&decoded); err != nil {
				return err
			}
			_, err := portableNumbers(decoded)
			return err
		}
		if number, ok := v.Interface().(json.Number); ok {
			_, err := portableNumbers(number)
			return err
		}
	}
	switch v.Kind() {
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64, reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Float32, reflect.Float64:
		if dynamic {
			wire, err := json.Marshal(v.Interface())
			if err != nil {
				return err
			}
			_, err = portableNumbers(json.Number(wire))
			return err
		}
	case reflect.Struct:
		for i := 0; i < v.NumField(); i++ {
			if v.Type().Field(i).PkgPath == "" && v.Type().Field(i).Tag.Get("json") != "-" {
				if err := validateDynamicNumber(v.Field(i), dynamic, depth+1); err != nil {
					return err
				}
			}
		}
	case reflect.Map:
		for _, key := range v.MapKeys() {
			if err := validateDynamicNumber(v.MapIndex(key), dynamic, depth+1); err != nil {
				return err
			}
		}
	case reflect.Array, reflect.Slice:
		for i := 0; i < v.Len(); i++ {
			if err := validateDynamicNumber(v.Index(i), dynamic, depth+1); err != nil {
				return err
			}
		}
	}
	return nil
}

func portableNumbers(value any) (any, error) {
	switch v := value.(type) {
	case json.Number:
		n, err := v.Float64()
		if err != nil || math.IsNaN(n) || math.IsInf(n, 0) || math.Abs(n) > 9007199254740991 {
			return nil, fmt.Errorf("%w: numeric argument exceeds portable JSON precision; use a string", ErrInvalid)
		}
		original, ok := new(big.Rat).SetString(v.String())
		canonical, canonicalOK := new(big.Rat).SetString(strconv.FormatFloat(n, 'g', -1, 64))
		if !ok || !canonicalOK || original.Cmp(canonical) != 0 {
			return nil, fmt.Errorf("%w: numeric argument loses JSON precision", ErrInvalid)
		}
		return n, nil
	case map[string]any:
		for key, item := range v {
			normalized, err := portableNumbers(item)
			if err != nil {
				return nil, err
			}
			v[key] = normalized
		}
	case []any:
		for i, item := range v {
			normalized, err := portableNumbers(item)
			if err != nil {
				return nil, err
			}
			v[i] = normalized
		}
	}
	return value, nil
}

func (b Basis) Seal() (Basis, error) {
	b.Digest = ""
	digest, err := Fingerprint(b)
	if err != nil {
		return Basis{}, err
	}
	b.Digest = digest
	return b, b.Validate()
}

func (b Basis) Validate() error {
	if b.SchemaVersion != SchemaVersion || b.Scope != IntentScope || b.TaskID == "" || b.TaskRevision == 0 ||
		b.AggregateVersion == 0 || b.ClaimVersion == 0 || b.IntentIndex < 0 || b.StepID == "" || b.RobotID == "" ||
		b.Adapter == "" || b.CommandID != CommandID(b.TaskID, b.TaskRevision, b.StepID) {
		return fmt.Errorf("%w: identity/schema incomplete", ErrInvalid)
	}
	if decoded, err := hex.DecodeString(b.ExecutionDigest); err != nil || len(decoded) != sha256.Size {
		return fmt.Errorf("%w: execution digest missing", ErrInvalid)
	}
	if (b.ResourceID == "") != (b.FencingToken == 0) {
		return fmt.Errorf("%w: resource/fence incomplete", ErrInvalid)
	}
	switch b.World.Mode {
	case "coordinator_world":
		if b.World.WorldID == "" {
			return fmt.Errorf("%w: world identity missing", ErrInvalid)
		}
	case "edge_local":
		if b.World != (WorldBasis{Mode: "edge_local"}) {
			return fmt.Errorf("%w: mixed world basis", ErrInvalid)
		}
	default:
		return fmt.Errorf("%w: world authority missing", ErrInvalid)
	}
	digest := b.Digest
	b.Digest = ""
	expected, err := Fingerprint(b)
	if err != nil || expected != digest {
		return fmt.Errorf("%w: digest mismatch", ErrInvalid)
	}
	return nil
}
