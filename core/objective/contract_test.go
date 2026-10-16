package objective

import (
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"strings"
	"testing"
	"time"
)

func TestObjectiveContractRejectsScopeExpansionAndMalformedEnvelope(t *testing.T) {
	base := Contract{SchemaVersion: Version, RobotID: "r1", Goal: "检查地图", CatalogRevision: strings.Repeat("a", 64), Grants: []Grant{{Tool: "mapping.status", Arguments: map[string]any{"type": "object", "additionalProperties": false}}}, Effects: []string{"READ"}, Resources: []string{"robot"}, Completion: []Check{{Tool: "mapping.status", Arguments: map[string]any{}, Path: []string{"ready"}, Equals: true}}, Budget: Budget{MaxPlans: 2, MaxSteps: 4, MaxReads: 32, TimeoutSeconds: 60}, ExpiresAt: time.Now().Add(time.Hour)}
	if err := base.Validate(); err != nil {
		t.Fatal(err)
	}
	restored, err := Decode(base.Call())
	if err != nil || capability.Fingerprint(base) != capability.Fingerprint(restored) {
		t.Fatalf("wire identity differs: %v", err)
	}
	for _, mutate := range []func(*Contract){func(c *Contract) { c.Resources = []string{"shared-object"} }, func(c *Contract) { c.Budget.MaxPlans = 100 }, func(c *Contract) { c.Completion = nil }, func(c *Contract) { c.Grants[0].Tool = Tool }, func(c *Contract) { c.Effects = []string{"SAFETY_STOP"} }} {
		c, _ := Decode(base.Call())
		mutate(&c)
		if c.Validate() == nil {
			t.Fatal("invalid contract accepted")
		}
	}
	if base.Arguments("mapping.status", map[string]any{"robotId": "another-robot"}) == nil {
		t.Fatal("arguments expanded scope")
	}
	if base.Arguments("mapping.build", map[string]any{}) == nil {
		t.Fatal("tool expanded scope")
	}
}
