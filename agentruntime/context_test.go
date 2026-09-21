package agentruntime

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

func TestAdvisoryContextPersistsWithoutPromotingModelClaims(t *testing.T) {
	t.Setenv("TANGYING_AGENT_CONTEXT", "json")
	request := RecoveryRequest{Diagnosis: "需要复核", Facts: RecoveryFacts{TaskID: "t1", ReconciliationUnavailable: true}, Trail: &Trail{ID: "trail1", Steps: []TrailStep{{Sequence: 1, Kind: TrailModel, Summary: "模型猜测已成功"}}}}
	doc := request.ContextDocument(time.Unix(100, 0))
	if doc.Records[1].Kind != "hypothesis" {
		t.Fatal("model guess became fact")
	}
	event := agentcontract.Event{TaskID: "t1", Topic: agentcontract.TopicOpsRecoveryExecuted, Payload: map[string]any{"agent_context": contextEnvelope(doc)}}
	durable, ok := TaskEventFromEvent(event)
	if !ok {
		t.Fatal("context event not persistable")
	}
	raw, err := json.Marshal(durable)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "sha256") || !strings.Contains(string(raw), "ReconciliationUnavailable") {
		t.Fatal("lost replayable context")
	}
}
