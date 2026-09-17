package pairing

import (
	"bytes"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"testing"
)

// HKDF is the one thing here that is written out rather than imported, so it is
// checked against the standard's own vectors before anything is built on it.
//
// The vectors are RFC 5869 appendix A, quoted verbatim. A key-derivation function
// that is subtly wrong does not fail loudly: it produces keys that work perfectly
// on one side and not at all on the other, and the symptom is "pairing silently
// does not work". That is why these are the first tests in this package.
func TestHKDFMatchesTheRFC5869Vectors(t *testing.T) {
	for _, vector := range []struct {
		name   string
		ikm    string
		salt   string
		info   string
		length int
		want   string
	}{
		{
			name:   "A.1 basic SHA-256",
			ikm:    "0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
			salt:   "000102030405060708090a0b0c",
			info:   "f0f1f2f3f4f5f6f7f8f9",
			length: 42,
			want:   "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865",
		},
		{
			name: "A.2 longer inputs and output",
			ikm: "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f" +
				"202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f" +
				"404142434445464748494a4b4c4d4e4f",
			salt: "606162636465666768696a6b6c6d6e6f707172737475767778797a7b7c7d7e7f" +
				"808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f" +
				"a0a1a2a3a4a5a6a7a8a9aaabacadaeaf",
			info: "b0b1b2b3b4b5b6b7b8b9babbbcbdbebfc0c1c2c3c4c5c6c7c8c9cacbcccdcecf" +
				"d0d1d2d3d4d5d6d7d8d9dadbdcdddedfe0e1e2e3e4e5e6e7e8e9eaebecedeeef" +
				"f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff",
			length: 82,
			want: "b11e398dc80327a1c8e7f78c596a49344f012eda2d4efad8a050cc4c19afa97c" +
				"59045a99cac7827271cb41c65e590e09da3275600c2f09b8367793a9aca3db71" +
				"cc30c58179ec3e87c14c01d5c1f3434f1d87",
		},
		{
			name:   "A.3 zero-length salt and info",
			ikm:    "0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
			salt:   "",
			info:   "",
			length: 42,
			want:   "8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d9d201395faa4b61a96c8",
		},
	} {
		t.Run(vector.name, func(t *testing.T) {
			got := hkdfSHA256(mustHex(t, vector.ikm), mustHex(t, vector.salt), mustHex(t, vector.info), vector.length)
			if hex.EncodeToString(got) != vector.want {
				t.Fatalf("HKDF output mismatch\n got: %s\nwant: %s", hex.EncodeToString(got), vector.want)
			}
		})
	}
}

func mustHex(t *testing.T, value string) []byte {
	t.Helper()
	if value == "" {
		return nil
	}
	decoded, err := hex.DecodeString(value)
	if err != nil {
		t.Fatalf("bad hex in the test vector: %v", err)
	}
	return decoded
}

// The code is read off a label by a person, so the same code arrives written in
// several ways and all of them have to work.
func TestTheCodeIsNormalisedTheSameWayOnBothSides(t *testing.T) {
	for _, variant := range []string{"4F2K-9QW7", "4f2k9qw7", " 4F2K 9QW7 ", "4f2k_9qw7"} {
		if got := NormalizeCode(variant); got != "4F2K9QW7" {
			t.Fatalf("NormalizeCode(%q) = %q", variant, got)
		}
	}
	// And an empty code never authorises anything.
	if _, err := DeriveKey("", []byte("salt")); err == nil {
		t.Fatal("an empty pairing code derived a key")
	}
	if _, err := DeriveKey("---", []byte("salt")); err == nil {
		t.Fatal("a code that normalises to nothing derived a key")
	}
}

func TestTheSameCodeInDifferentSpellingsReachesTheSameKey(t *testing.T) {
	salt := []byte("0123456789abcdef0123456789abcdef")
	left, err := DeriveKey("4F2K-9QW7", salt)
	if err != nil {
		t.Fatalf("derive: %v", err)
	}
	right, err := DeriveKey("4f2k9qw7", salt)
	if err != nil {
		t.Fatalf("derive: %v", err)
	}
	if !bytes.Equal(left, right) {
		t.Fatal("the same code written two ways produced two keys")
	}
}

func TestAFreshSaltProducesAFreshKey(t *testing.T) {
	// Two pairings of the same robot must not derive the same key, or a recorded
	// attempt could be replayed later.
	first, err := DeriveKey("4F2K9QW7", []byte("salt-one-0123456789abcdefghijkl"))
	if err != nil {
		t.Fatalf("derive: %v", err)
	}
	second, err := DeriveKey("4F2K9QW7", []byte("salt-two-0123456789abcdefghijkl"))
	if err != nil {
		t.Fatalf("derive: %v", err)
	}
	if bytes.Equal(first, second) {
		t.Fatal("two salts produced one key")
	}
}

func TestSealedMaterialOpensOnlyWithTheRightCode(t *testing.T) {
	salt, err := NewSalt()
	if err != nil {
		t.Fatalf("salt: %v", err)
	}
	nonce, err := NewNonce()
	if err != nil {
		t.Fatalf("nonce: %v", err)
	}
	secret := []byte(`{"ca":"-----BEGIN CERTIFICATE-----"}`)
	sealed, err := Seal("4F2K-9QW7", salt, nonce, "xlerobot-0001", secret)
	if err != nil {
		t.Fatalf("seal: %v", err)
	}
	opened, err := Open("4f2k9qw7", salt, nonce, "xlerobot-0001", sealed)
	if err != nil {
		t.Fatalf("open with the right code: %v", err)
	}
	if !bytes.Equal(opened, secret) {
		t.Fatal("the payload did not survive the round trip")
	}
	if _, err := Open("WRONG-CODE", salt, nonce, "xlerobot-0001", sealed); err == nil {
		t.Fatal("a wrong code opened the payload")
	}
}

// The ciphertext is bound to one robot.
//
// Without this, a request captured while pairing robot A could be replayed at
// robot B by anyone who had also learned B's code, and "the code is per robot"
// would be a claim the cryptography did not make.
func TestSealedMaterialCannotBeReplayedAtAnotherRobot(t *testing.T) {
	salt, _ := NewSalt()
	nonce, _ := NewNonce()
	sealed, err := Seal("4F2K9QW7", salt, nonce, "xlerobot-0001", []byte("material"))
	if err != nil {
		t.Fatalf("seal: %v", err)
	}
	if _, err := Open("4F2K9QW7", salt, nonce, "xlerobot-0002", sealed); err == nil {
		t.Fatal("a pairing request for one robot opened at another robot")
	}
}

// A tampered ciphertext is refused rather than half-applied.
func TestTamperedMaterialIsRefused(t *testing.T) {
	salt, _ := NewSalt()
	nonce, _ := NewNonce()
	sealed, err := Seal("4F2K9QW7", salt, nonce, "xlerobot-0001", []byte("material"))
	if err != nil {
		t.Fatalf("seal: %v", err)
	}
	tampered := []byte(sealed)
	if tampered[0] == 'A' {
		tampered[0] = 'B'
	} else {
		tampered[0] = 'A'
	}
	if _, err := Open("4F2K9QW7", salt, nonce, "xlerobot-0001", string(tampered)); err == nil {
		t.Fatal("a tampered payload was accepted")
	}
}

// The comparison does not stop at the first difference.
//
// A short code guessed one byte at a time is the attack this prevents.
func TestCodeComparisonIsConstantTimeAndNormalised(t *testing.T) {
	if !EqualCodes("4F2K-9QW7", "4f2k9qw7") {
		t.Fatal("the same code written two ways compared unequal")
	}
	if EqualCodes("4F2K9QW7", "4F2K9QW8") {
		t.Fatal("a different code compared equal")
	}
	// A differing length must not panic or read out of bounds.
	if EqualCodes("4F2K9QW7", "4F2K") {
		t.Fatal("a prefix compared equal")
	}
	if EqualCodes("", "") {
		t.Fatal("two empty codes compared equal")
	}
}

// Frames carry any byte, including ones a terminator would have used.
func TestFramesSurviveEveryByteValue(t *testing.T) {
	payload := make([]byte, 256)
	for index := range payload {
		payload[index] = byte(index)
	}
	var buffer bytes.Buffer
	if err := WriteFrame(&buffer, payload); err != nil {
		t.Fatalf("write frame: %v", err)
	}
	decoded, err := ReadFrame(&buffer)
	if err != nil {
		t.Fatalf("read frame: %v", err)
	}
	if !bytes.Equal(decoded, payload) {
		t.Fatal("the framed payload changed")
	}
}

// An oversized frame is refused before it is allocated.
func TestAnOversizedFrameIsRefused(t *testing.T) {
	var buffer bytes.Buffer
	// A header claiming 4 GiB, with no body behind it.
	buffer.Write([]byte{0xFF, 0xFF, 0xFF, 0xFF})
	if _, err := ReadFrame(&buffer); err == nil {
		t.Fatal("an out-of-range frame length was accepted")
	}
}

// A request is refused when it is not one, and when it speaks another version.
func TestRequestsFromElsewhereAreRefused(t *testing.T) {
	for name, payload := range map[string]string{
		"empty":       ``,
		"not json":    `hello`,
		"wrong topic": `{"topic":"printer.enroll","version":1,"robotId":"r"}`,
		"no robot id": `{"topic":"tangying.robot.enroll","version":1,"salt":"AAAA","nonce":"AAAAAAAAAAAAAAAA","payload":"AAAA"}`,
		"bad base64":  `{"topic":"tangying.robot.enroll","version":1,"robotId":"r","salt":"!!","nonce":"AAAAAAAAAAAAAAAA","payload":"AAAA"}`,
	} {
		if _, _, err := DecodeRequest("4F2K9QW7", []byte(payload)); err == nil {
			t.Fatalf("%s was accepted as an enrollment request", name)
		}
	}
	_, _, err := DecodeRequest("4F2K9QW7",
		[]byte(`{"topic":"tangying.robot.enroll","version":99,"robotId":"r","salt":"AAAA","nonce":"AAAAAAAAAAAAAAAA","payload":"AAAA"}`))
	if err == nil {
		t.Fatal("an enrollment request from an unknown protocol version was accepted")
	}
}

// The whole exchange, both directions, in one test.
func TestAFullExchangeRoundTrips(t *testing.T) {
	code := "4F2K-9QW7"
	robotID := "xlerobot-0001"
	material := Material{
		CA:         "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----\n",
		ServerCert: "-----BEGIN CERTIFICATE-----\nserver\n-----END CERTIFICATE-----\n",
		ServerKey:  "-----BEGIN EC PRIVATE KEY-----\nkey\n-----END EC PRIVATE KEY-----\n",
	}
	requestBytes, err := EncodeRequest(code, material, robotID)
	if err != nil {
		t.Fatalf("encode request: %v", err)
	}

	// The robot side.
	decoded, request, err := DecodeRequest(code, requestBytes)
	if err != nil {
		t.Fatalf("the robot refused a valid request: %v", err)
	}
	if decoded != material {
		t.Fatal("the material did not survive the round trip")
	}
	if request.RobotID != robotID {
		t.Fatalf("robot id = %q", request.RobotID)
	}
	salt, err := base64.StdEncoding.DecodeString(request.Salt)
	if err != nil {
		t.Fatalf("salt: %v", err)
	}
	answerNonce, _ := NewNonce()
	answer, err := EncodeResult(code, salt, answerNonce, robotID, ResultBody{Status: "paired"})
	if err != nil {
		t.Fatalf("encode result: %v", err)
	}

	// Back on the agent side.
	body, err := DecodeResult(code, salt, robotID, answer)
	if err != nil {
		t.Fatalf("the agent could not read the answer: %v", err)
	}
	if body.Status != "paired" {
		t.Fatalf("status = %q", body.Status)
	}
}

// A wrong code fails at the robot, which is where it has to fail: the agent's
// attempt must not be able to install anything.
func TestAWrongCodeFailsBeforeAnythingIsInstalled(t *testing.T) {
	requestBytes, err := EncodeRequest("4F2K-9QW7", Material{
		CA: "ca", ServerCert: "cert", ServerKey: "key",
	}, "xlerobot-0001")
	if err != nil {
		t.Fatalf("encode request: %v", err)
	}
	_, _, err = DecodeRequest("AAAA-BBBB", requestBytes)
	if err == nil {
		t.Fatal("a request sealed with another code was accepted")
	}
	if !errors.Is(err, ErrWrongCode) {
		t.Fatalf("error = %v, want it reported as a wrong code", err)
	}
}
