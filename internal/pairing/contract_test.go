package pairing

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// The fixture is the contract with the robot.
//
// The robot's pairing implementation is Python and this one is Go, and the
// agreement between them is a key-derivation function and a cipher. Nothing in
// either language's compiler checks the other, and the failure mode is silent:
// keys that work perfectly on one side and not at all on the other, which
// presents as "pairing does not work" with no clue why.
//
// The Python side asserts that what it produces matches this file byte for byte;
// this side asserts it can open what the fixture contains. A change to either
// side that the other does not expect fails a test here instead of failing in
// somebody's living room.
const pairingFixturePath = "../../tests/contract/robot_pairing.json"

type pairingFixture struct {
	Code        string            `json:"code"`
	RobotID     string            `json:"robotId"`
	Salt        string            `json:"salt"`
	Nonce       string            `json:"nonce"`
	AnswerNonce string            `json:"answerNonce"`
	DerivedKey  string            `json:"derivedKey"`
	Material    Material          `json:"material"`
	Request     Request           `json:"request"`
	Answer      Result            `json:"answer"`
	ResultBody  ResultBody        `json:"resultBody"`
	Extra       map[string]string `json:"-"`
}

func readPairingFixture(t *testing.T) pairingFixture {
	t.Helper()
	data, err := os.ReadFile(filepath.Clean(pairingFixturePath))
	if err != nil {
		t.Fatalf("read the pairing fixture: %v", err)
	}
	var fixture pairingFixture
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatalf("the fixture is not the expected JSON: %v", err)
	}
	if fixture.Code == "" || fixture.RobotID == "" || fixture.DerivedKey == "" {
		t.Fatalf("the fixture is missing required fields: %+v", fixture)
	}
	return fixture
}

func decodeBase64(t *testing.T, value string) []byte {
	t.Helper()
	decoded, err := base64.StdEncoding.DecodeString(value)
	if err != nil {
		t.Fatalf("bad base64 in the fixture: %v", err)
	}
	return decoded
}

// The two implementations must derive the same key from the same code and salt.
//
// This is the assertion that makes a hand-written HKDF acceptable: if Python's
// HKDF and this one agree on the fixture, and both match the RFC's own vectors,
// then the derivation is the standard's and not an approximation of it.
func TestTheSameCodeAndSaltDeriveTheSameKeyOnBothSides(t *testing.T) {
	fixture := readPairingFixture(t)
	expected := decodeBase64(t, fixture.DerivedKey)
	got, err := DeriveKey(fixture.Code, decodeBase64(t, fixture.Salt))
	if err != nil {
		t.Fatalf("derive: %v", err)
	}
	if len(got) != len(expected) {
		t.Fatalf("derived key is %d bytes, the robot produced %d", len(got), len(expected))
	}
	for index := range got {
		if got[index] != expected[index] {
			t.Fatalf("derived keys differ at byte %d: this side and the robot disagree on HKDF", index)
		}
	}
}

// This side must be able to read a request the robot produced.
func TestTheRobotCanReadWhatThisSideSeals(t *testing.T) {
	fixture := readPairingFixture(t)
	salt := decodeBase64(t, fixture.Salt)
	nonce := decodeBase64(t, fixture.Nonce)

	// What this side produces, opened by this side.
	sealed, err := Seal(fixture.Code, salt, nonce, fixture.RobotID, mustJSON(t, fixture.Material))
	if err != nil {
		t.Fatalf("seal: %v", err)
	}
	opened, err := Open(fixture.Code, salt, nonce, fixture.RobotID, sealed)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	var decoded Material
	if err := json.Unmarshal(opened, &decoded); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if decoded != fixture.Material {
		t.Fatal("the material did not survive")
	}

	// And the payload the fixture records from Python opens here, which is the
	// half that actually crosses the language boundary.
	if _, err := Open(fixture.Code, salt, nonce, fixture.RobotID, fixture.Request.Payload); err != nil {
		t.Fatalf("this side could not open the request the robot side produced: %v", err)
	}
}

// And a request the robot recorded must decode into the same material.
func TestTheFixtureRequestDecodesToItsMaterial(t *testing.T) {
	fixture := readPairingFixture(t)
	encoded, err := json.Marshal(fixture.Request)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	material, request, err := DecodeRequest(fixture.Code, encoded)
	if err != nil {
		t.Fatalf("decode the robot's request: %v", err)
	}
	if material != fixture.Material {
		t.Fatalf("material = %+v, want %+v", material, fixture.Material)
	}
	if request.RobotID != fixture.RobotID {
		t.Fatalf("robot id = %q", request.RobotID)
	}
}

// The robot's answer must open here, because that is how the agent learns the
// pairing succeeded rather than assuming it.
func TestTheRobotsAnswerOpensOnThisSide(t *testing.T) {
	fixture := readPairingFixture(t)
	encoded, err := json.Marshal(fixture.Answer)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	body, err := DecodeResult(fixture.Code, decodeBase64(t, fixture.Salt), fixture.RobotID, encoded)
	if err != nil {
		t.Fatalf("decode the robot's answer: %v", err)
	}
	if body.Status != fixture.ResultBody.Status {
		t.Fatalf("status = %q, want %q", body.Status, fixture.ResultBody.Status)
	}
}

// The fixture must not be openable with the wrong code, or the fixture would be
// proving something weaker than the protocol claims.
func TestTheFixtureRefusesTheWrongCode(t *testing.T) {
	fixture := readPairingFixture(t)
	encoded, err := json.Marshal(fixture.Request)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if _, _, err := DecodeRequest("AAAA-BBBB", encoded); err == nil {
		t.Fatal("the fixture request opened under a different code")
	}
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	return encoded
}
