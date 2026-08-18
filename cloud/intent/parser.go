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
var englishFetch = regexp.MustCompile(`(?i)(?:bring|fetch|hand)\s+(?:me\s+)?(?:the\s+)?(?:(red|blue|green)\s+)?(cup|bottle|block)(?:\s+(?:here|to\s+me))?`)
var sequenceSeparator = regexp.MustCompile(`(?i)\s*(?:and\s+then|then)\s*|；|;|。|，|然后|接着|之后|再`)

func (p *DeterministicParser) Parse(request string) (manipulation.Intent, error) {
	segments := splitSequence(request)
	if len(segments) > 1 {
		parsed := make([]manipulation.Intent, 0, len(segments))
		for _, segment := range segments {
			intent, err := p.parseSingle(segment)
			if err != nil {
				return manipulation.Intent{}, err
			}
			parsed = append(parsed, intent)
		}
		return sequenceIntent(parsed), nil
	}
	return p.parseSingle(request)
}

func (p *DeterministicParser) parseSingle(request string) (manipulation.Intent, error) {
	request = normalizeRequest(request)

	if parsed, ok := parseChinesePickPlace(request); ok {
		return parsed, nil
	}
	if parsed, ok := parseChineseFetch(request); ok {
		return parsed, nil
	}

	if matches := englishPickPlace.FindStringSubmatch(request); len(matches) == 5 {
		relation := ""
		if matches[3] != "" {
			relation = strings.ToLower(matches[3]) + "_side"
		}
		return buildPickPlace(strings.ToLower(matches[2]), strings.ToLower(matches[1]), relation), nil
	}
	if matches := englishFetch.FindStringSubmatch(request); len(matches) == 3 {
		return buildFetch(strings.ToLower(matches[2]), strings.ToLower(matches[1])), nil
	}
	return manipulation.Intent{}, ErrUnsupportedIntent
}

func splitSequence(request string) []string {
	parts := sequenceSeparator.Split(request, -1)
	segments := make([]string, 0, len(parts))
	for _, part := range parts {
		if strings.TrimSpace(part) != "" {
			segments = append(segments, part)
		}
	}
	return segments
}

func sequenceIntent(parsed []manipulation.Intent) manipulation.Intent {
	first := parsed[0]
	first.Sequence = append([]manipulation.Intent(nil), parsed...)
	return first
}

func normalizeRequest(request string) string {
	request = strings.TrimSpace(request)
	// Tolerate "让 xlerobot ..." style agent framing.
	for _, prefix := range []string{"让xlerobot", "让机器人", "请让机器人"} {
		request = strings.TrimPrefix(request, prefix)
	}
	return strings.TrimSpace(request)
}

func parseChinesePickPlace(request string) (manipulation.Intent, bool) {
	if !strings.HasPrefix(request, "把") || !strings.Contains(request, "放") {
		return manipulation.Intent{}, false
	}
	color := chineseColor(request)
	category := chineseCategory(request)
	if color == "" || category == "" || (!strings.Contains(request, "收纳盒") && !strings.Contains(request, "箱")) {
		return manipulation.Intent{}, false
	}
	relation := ""
	if strings.Contains(request, "右侧") || strings.Contains(request, "右边") {
		relation = "right_side"
	} else if strings.Contains(request, "左侧") || strings.Contains(request, "左边") {
		relation = "left_side"
	}
	return buildPickPlace(category, color, relation), true
}

func parseChineseFetch(request string) (manipulation.Intent, bool) {
	if !strings.HasPrefix(request, "把") {
		return manipulation.Intent{}, false
	}
	fetch := strings.Contains(request, "拿过来") || strings.Contains(request, "拿给我") ||
		strings.Contains(request, "递给我") || strings.Contains(request, "取过来") || strings.Contains(request, "给我")
	if !fetch {
		return manipulation.Intent{}, false
	}
	color := chineseColor(request)
	category := chineseCategory(request)
	if color == "" || category == "" {
		return manipulation.Intent{}, false
	}
	return buildFetch(category, color), true
}

func chineseCategory(request string) string {
	switch {
	case strings.Contains(request, "积木"), strings.Contains(request, "方块"):
		return "block"
	case strings.Contains(request, "瓶子"), strings.Contains(request, "瓶"):
		return "bottle"
	case strings.Contains(request, "杯子"), strings.Contains(request, "杯"):
		return "cup"
	default:
		return ""
	}
}

func chineseColor(request string) string {
	for chinese, normalized := range map[string]string{"红色": "red", "蓝色": "blue", "绿色": "green"} {
		if strings.Contains(request, chinese) {
			return normalized
		}
	}
	return ""
}

func buildPickPlace(category, color, relation string) manipulation.Intent {
	return manipulation.Intent{
		Action: manipulation.ActionPickAndPlace,
		Object: manipulation.EntitySelector{
			Category:   category,
			Attributes: map[string]string{"color": color},
		},
		Destination: manipulation.EntitySelector{Category: manipulation.CategoryStorageBin, Relation: relation},
		Constraints: manipulation.Constraints{KeepUpright: true, AvoidHumans: true},
	}
}

func buildFetch(category, color string) manipulation.Intent {
	return manipulation.Intent{
		Action: manipulation.ActionFetch,
		Object: manipulation.EntitySelector{
			Category:   category,
			Attributes: map[string]string{"color": color},
		},
		Destination: manipulation.EntitySelector{Category: manipulation.CategoryDeliveryTray, Relation: "front_side"},
		Constraints: manipulation.Constraints{KeepUpright: true, AvoidHumans: true},
	}
}
