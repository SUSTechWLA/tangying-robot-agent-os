package contextcontract

import (
	"encoding/json"
	"errors"
	"testing"
)

func validBasis(t *testing.T) Basis {
	t.Helper()
	digest, _ := Fingerprint(map[string]any{"action": "fixture-only"})
	b, err := (Basis{SchemaVersion: SchemaVersion, Scope: IntentScope, TaskID: "task", TaskRevision: 2, AggregateVersion: 5,
		ClaimVersion: 7, StepID: "step", CommandID: CommandID("task", 2, "step"), RobotID: "robot", Adapter: "fake", ExecutionDigest: digest,
		World: WorldBasis{Mode: "edge_local"}}).Seal()
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestBasisWireRoundTripAndMixedVersions(t *testing.T) {
	original := validBasis(t)
	wire, _ := json.Marshal(original)
	var decoded Basis
	if err := json.Unmarshal(wire, &decoded); err != nil {
		t.Fatal(err)
	}
	if err := decoded.Validate(); err != nil {
		t.Fatal(err)
	}
	for name, mutate := range map[string]func(*Basis){
		"scope":          func(b *Basis) { b.Scope = "another-scope" },
		"revision":       func(b *Basis) { b.TaskRevision++ },
		"robot":          func(b *Basis) { b.RobotID = "another" },
		"missing-digest": func(b *Basis) { b.ExecutionDigest = "" },
		"fence":          func(b *Basis) { b.FencingToken = 4 },
		"mixed-world":    func(b *Basis) { b.World.Revision = 2 },
	} {
		t.Run(name, func(t *testing.T) {
			copy := original
			mutate(&copy)
			if !errors.Is(copy.Validate(), ErrInvalid) {
				t.Fatal("mixed context accepted")
			}
		})
	}
}

func TestBasisRequiresExplicitWorldAuthority(t *testing.T) {
	b := validBasis(t)
	b.World = WorldBasis{}
	if _, err := b.Seal(); !errors.Is(err, ErrInvalid) {
		t.Fatalf("err=%v", err)
	}
	b.World = WorldBasis{Mode: "coordinator_world"}
	if _, err := b.Seal(); !errors.Is(err, ErrInvalid) {
		t.Fatalf("err=%v", err)
	}
}

func TestFingerprintPortableJSONNumbersAcrossDecode(t *testing.T) {
	value := map[string]any{"id": uint64(9007199254740991), "position": 0.125, "largeId": "9007199254740993"}
	if err := ValidatePortableArguments(value); err != nil {
		t.Fatal(err)
	}
	before, err := Fingerprint(value)
	if err != nil {
		t.Fatal(err)
	}
	wire, _ := json.Marshal(value)
	var decoded any
	if err := json.Unmarshal(wire, &decoded); err != nil {
		t.Fatal(err)
	}
	after, err := Fingerprint(decoded)
	if err != nil || before != after {
		t.Fatalf("before=%s after=%s err=%v", before, after, err)
	}
	for _, value := range []any{map[string]any{"id": uint64(9007199254740993)}, map[string]any{"id": json.Number("9007199254740993")}, json.RawMessage(`{"args":{"sequence":9007199254740993}}`), json.RawMessage(`{"position":0.1000000000000000001}`)} {
		if err := ValidatePortableArguments(value); !errors.Is(err, ErrInvalid) {
			t.Fatalf("nonportable %v accepted: %v", value, err)
		}
	}
	for _, value := range []any{map[string]any{"nested": []uint64{9007199254740993}}, map[string]any{"nested": map[string]uint64{"id": 9007199254740993}}, map[string]any{"nested": struct{ ID uint64 }{9007199254740993}}} {
		if err := ValidatePortableArguments(value); !errors.Is(err, ErrInvalid) {
			t.Fatalf("dynamic nested number accepted: %v", err)
		}
	}
}

func TestProtocolUint64FencingSurvivesTypedWireRoundTrip(t *testing.T) {
	b := validBasis(t)
	b.ResourceID = "robot:robot"
	b.FencingToken = uint64(1791514885123456789)
	b.World = WorldBasis{Mode: "coordinator_world", WorldID: "world", RobotSourceID: "robot/state", RobotSequence: 1791514885123456789}
	sealed, err := b.Seal()
	if err != nil {
		t.Fatal(err)
	}
	if err := ValidatePortableArguments(sealed); err != nil {
		t.Fatal(err)
	}
	wire, err := json.Marshal(sealed)
	if err != nil {
		t.Fatal(err)
	}
	var decoded Basis
	if err := json.Unmarshal(wire, &decoded); err != nil {
		t.Fatal(err)
	}
	if err := decoded.Validate(); err != nil || decoded.FencingToken != b.FencingToken || decoded.World.RobotSequence != b.World.RobotSequence {
		t.Fatalf("decoded=%+v err=%v", decoded, err)
	}
}

func TestPortableNumericSpellingDoesNotChangeExecutionFingerprint(t *testing.T) {
	before := map[string]any{"args": map[string]any{"distance": json.Number("1e3"), "ratio": json.Number("0.1000")}}
	if err := ValidatePortableArguments(before); err != nil {
		t.Fatal(err)
	}
	wire, _ := json.Marshal(before)
	var decoded any
	if err := json.Unmarshal(wire, &decoded); err != nil {
		t.Fatal(err)
	}
	one, err := Fingerprint(before)
	if err != nil {
		t.Fatal(err)
	}
	two, err := Fingerprint(decoded)
	if err != nil || one != two {
		t.Fatalf("equivalent arguments changed hash: %s != %s (%v)", one, two, err)
	}
}
