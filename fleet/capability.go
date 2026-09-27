package fleet

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/registry"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"google.golang.org/protobuf/encoding/protojson"
)

// CapabilityPlanner plans against the robot's authenticated advertisement. The
// server has no Runtime command transport. Execution stays with the leased edge.
type CapabilityPlanner struct {
	Registry *registry.Registry
	Decider  actionloop.Decider
	Parser   intent.Parser
}

type advertisedProvider struct{ catalog *robotv1.ServiceCatalog }

func (p advertisedProvider) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	return p.catalog, nil
}
func (p advertisedProvider) CallService(context.Context, *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	return nil, fmt.Errorf("cloud planning cannot execute robot services")
}

func (p *CapabilityPlanner) PlanGoal(ctx context.Context, request string) (orchestration.Bundle, bool, error) {
	// Preserve existing multi-robot intent grammar when generic goals are not requested.
	generic := p.Decider != nil
	for _, word := range []string{"标定", "建图", "地图", "探索"} {
		generic = generic || strings.Contains(request, word)
	}
	if !generic {
		return orchestration.Bundle{}, false, nil
	}
	devices, err := p.Registry.List(ctx)
	if err != nil {
		return orchestration.Bundle{}, true, err
	}
	target, goal, explicit := strings.Cut(request, ":")
	if !explicit {
		target, goal, explicit = strings.Cut(request, "：")
	}
	var selected *registry.Device
	for i := range devices {
		d := &devices[i]
		if !d.Online || len(d.ServiceCatalog) == 0 || string(d.ServiceCatalog) == "null" {
			continue
		}
		if explicit {
			if d.RobotID == strings.TrimSpace(target) {
				selected = d
				break
			}
			continue
		}
		if selected != nil {
			return orchestration.Bundle{}, true, fmt.Errorf("%w: multiple online robots; prefix goal with robotId: ", intent.ErrClarificationRequired)
		}
		selected = d
	}
	if selected == nil {
		return orchestration.Bundle{}, true, fmt.Errorf("%w: select an online robot with a registered service catalog", intent.ErrClarificationRequired)
	}
	if !explicit {
		goal = request
	}
	catalog := &robotv1.ServiceCatalog{}
	if err := protojson.Unmarshal(selected.ServiceCatalog, catalog); err != nil {
		return orchestration.Bundle{}, true, fmt.Errorf("invalid device service catalog: %w", err)
	}
	if catalog.RobotId != selected.RobotID {
		return orchestration.Bundle{}, true, fmt.Errorf("service catalog identity mismatch")
	}
	planner := &capabilityagent.Planner{Provider: advertisedProvider{catalog}, Decider: p.Decider}
	if p.Parser != nil {
		planner.ParseLegacy = func(text string) (json.RawMessage, error) {
			parsed, err := p.Parser.Parse(text)
			if err != nil {
				return nil, err
			}
			parsed.RobotID = selected.RobotID
			for i := range parsed.Sequence {
				parsed.Sequence[i].RobotID = selected.RobotID
			}
			return json.Marshal(parsed)
		}
	}
	return planner.PlanGoal(ctx, strings.TrimSpace(goal))
}
