package robotclient

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

// ErrContractChanged requires deployment reconciliation, never an automatic repin.
var ErrContractChanged = errors.New("durable robot execution contract rejected")

type contractPin struct {
	SchemaVersion      string `json:"schemaVersion"`
	RobotID            string `json:"robotId"`
	Adapter            string `json:"adapter"`
	AdapterVersion     string `json:"adapterVersion"`
	ProtocolVersion    string `json:"protocolVersion"`
	CatalogRevision    string `json:"catalogRevision"`
	ProfileDigest      string `json:"profileDigest"`
	CapabilitiesDigest string `json:"capabilitiesDigest"`
	SafetyProfile      string `json:"safetyProfile"`
}

func executionContract(s runtime.Snapshot, safetyProfile string) contractPin {
	capabilities := append([]runtime.Capability(nil), s.Capabilities...)
	for i := range capabilities {
		// Availability is live state, not a new approved execution contract.
		capabilities[i].Available = false
		capabilities[i].Blockers = nil
	}
	sort.Slice(capabilities, func(i, j int) bool { return capabilities[i].Name < capabilities[j].Name })
	return contractPin{"edge.runtime-pin.v1", s.RobotID, s.Adapter, s.AdapterVersion,
		s.ProtocolVersion, s.CatalogRevision, digest(s.RobotProfile), digest(capabilities), safetyProfile}
}

func (c *Client) acceptDurableContract(s runtime.Snapshot) error {
	if c.expectedRobotID != "" && c.expectedRobotID != s.RobotID {
		return fmt.Errorf("%w: configured robot identity mismatch", ErrContractChanged)
	}
	if c.contractPinPath == "" {
		c.pinMu.Lock()
		c.admittedRobotID = s.RobotID
		c.pinSeen = s.RobotID != ""
		c.pinMu.Unlock()
		return nil
	}
	c.pinMu.Lock()
	defer c.pinMu.Unlock()
	fail := func(err error) error { return fmt.Errorf("%w: %v", ErrContractChanged, err) }
	if s.RobotID == "" || s.Adapter == "" || s.AdapterVersion == "" || s.CatalogRevision == "" {
		return fail(errors.New("runtime omitted execution identity"))
	}
	want := executionContract(s, c.profile)
	wire, err := json.Marshal(want)
	if err != nil {
		return fail(err)
	}
	path := c.contractPinPath
	if !filepath.IsAbs(path) {
		return fail(errors.New("pin path must be absolute"))
	}
	if _, err = os.Lstat(path); errors.Is(err, os.ErrNotExist) {
		if c.pinSeen {
			return fail(errors.New("pin disappeared while agent was running"))
		}
		if c.contractHistoryPath != "" && !c.admitMissingContract {
			history, historyErr := os.Lstat(c.contractHistoryPath)
			if historyErr == nil && (!history.Mode().IsRegular() || history.Size() > 0) {
				return fail(errors.New("execution history exists but runtime pin is missing; explicit no-motion admission is required"))
			}
			if historyErr != nil && !errors.Is(historyErr, os.ErrNotExist) {
				return fail(historyErr)
			}
		}
		// Publish a fully flushed inode without replacing a concurrent admission.
		if err = publishContractPin(path, append(wire, '\n')); err != nil && !errors.Is(err, os.ErrExist) {
			return fail(err)
		}
	} else if err != nil {
		return fail(err)
	}
	info, err := os.Lstat(path)
	if err != nil {
		return fail(err)
	}
	if !info.Mode().IsRegular() || info.Size() > 16384 {
		return fail(errors.New("pin is not a bounded regular file"))
	}
	f, err := os.Open(path)
	if err != nil {
		return fail(err)
	}
	contents, readErr := io.ReadAll(io.LimitReader(f, 16385))
	closeErr := f.Close()
	if readErr != nil {
		return fail(readErr)
	}
	if closeErr != nil {
		return fail(closeErr)
	}
	var actual contractPin
	decoder := json.NewDecoder(bytes.NewReader(contents))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&actual); err != nil {
		return fail(err)
	}
	if decoder.Decode(new(any)) != io.EOF {
		return fail(errors.New("invalid trailing pin data"))
	}
	if actual != want {
		return fail(errors.New("identity, profile, capability catalogue or safety profile changed; preserve journals and reconcile before recommissioning"))
	}
	c.pinSeen = true
	c.admittedRobotID = s.RobotID
	return nil
}

func publishContractPin(path string, data []byte) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, ".runtime-pin-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err = os.Link(f.Name(), path); err != nil {
		return err
	}
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}
