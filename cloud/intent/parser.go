package intent

import (
	"errors"
	"regexp"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

var ErrUnsupportedIntent = errors.New("unsupported intent")

type Parser interface {
	Parse(string) (manipulation.Intent, error)
}

type DeterministicParser struct{}

func NewDeterministicParser() *DeterministicParser { return &DeterministicParser{} }

var englishPickPlace = regexp.MustCompile(`(?i)(?:put|place|move)\s+(?:the\s+)?(?:(red|blue|green)\s+)?(cup|bottle|block)\s+(?:in|into|to)\s+(?:the\s+)?(?:(right|left)\s+)?(?:storage\s+)?(bin|box)`)

func (p *DeterministicParser) Parse(request string) (manipulation.Intent, error) {
	request = strings.TrimSpace(request)
	if strings.HasPrefix(request, "把") && strings.Contains(request, "放") {
		color := chineseColor(request)
		if color == "" || !strings.Contains(request, "杯") || (!strings.Contains(request, "收纳盒") && !strings.Contains(request, "箱")) {
			return manipulation.Intent{}, ErrUnsupportedIntent
		}
		relation := ""
		if strings.Contains(request, "右侧") || strings.Contains(request, "右边") {
			relation = "right_side"
		} else if strings.Contains(request, "左侧") || strings.Contains(request, "左边") {
			relation = "left_side"
		}
		return buildIntent("cup", color, "storage_bin", relation), nil
	}

	matches := englishPickPlace.FindStringSubmatch(request)
	if len(matches) == 5 {
		relation := ""
		if matches[3] != "" {
			relation = strings.ToLower(matches[3]) + "_side"
		}
		return buildIntent(strings.ToLower(matches[2]), strings.ToLower(matches[1]), "storage_bin", relation), nil
	}
	return manipulation.Intent{}, ErrUnsupportedIntent
}

func chineseColor(request string) string {
	for chinese, normalized := range map[string]string{"红色": "red", "蓝色": "blue", "绿色": "green"} {
		if strings.Contains(request, chinese) {
			return normalized
		}
	}
	return ""
}

func buildIntent(category, color, destination, relation string) manipulation.Intent {
	return manipulation.Intent{
		Action: "pick_and_place",
		Object: manipulation.EntitySelector{
			Category:   category,
			Attributes: map[string]string{"color": color},
		},
		Destination: manipulation.EntitySelector{Category: destination, Relation: relation},
		Constraints: manipulation.Constraints{KeepUpright: true, AvoidHumans: true},
	}
}
