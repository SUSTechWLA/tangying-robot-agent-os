package intent

import (
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

var ErrUnsupportedIntent = errors.New("unsupported intent")

// ErrClarificationRequired stops fallback models from dropping unresolved constraints.
var ErrClarificationRequired = fmt.Errorf("%w: clarification required", ErrUnsupportedIntent)

type Parser interface {
	Parse(string) (manipulation.Intent, error)
}
type DeterministicParser struct{}

func NewDeterministicParser() *DeterministicParser { return &DeterministicParser{} }

var (
	sequenceSeparator   = regexp.MustCompile(`(?i)\s*\b(?:and\s+then|then)\b\s*|；|;|。|，|然后|接着|之后|再`)
	nonAffirmative      = regexp.MustCompile(`(?i)\b(?:do\s+not|don['’]t|never|not|unless|if|until|stop|cancel|without)\b|不要|不允许|不许|禁止|不能|不可|不再|别(?:放|拿|送|移|动)|如果|除非|否则|取消|停止|暂停|等.+再`)
	robotChinese        = regexp.MustCompile(`^(?:让|由)?\s*([1-9][0-9]*|一|二|两|三|四|五|六|七|八|九|十)\s*号\s*机器人\s*`)
	robotChineseReverse = regexp.MustCompile(`^(?:让|由)?\s*机器人\s*([1-9][0-9]*|一|二|两|三|四|五|六|七|八|九|十)\s*号?\s*`)
	robotEnglish        = regexp.MustCompile(`(?i)^robot\s+([1-9][0-9]*)\s*[:,]?\s*`)
	chineseMove         = regexp.MustCompile(`^(?:把|将)(.+?)(?:放到|放进|放入|放在|移到|送到)(.+)$`)
	chineseFromMove     = regexp.MustCompile(`^从(.+?)(?:把|将)(.+?)(?:放到|放进|放入|放在|移到|送到)(.+)$`)
	chineseBareMove     = regexp.MustCompile(`^(?:放到|放进|放入|放在|移到|送到)(.+)$`)
	chineseFetch        = regexp.MustCompile(`^(?:把|将)(.+?)(?:拿过来|拿给我|递给我|取过来|给我)$`)
	chineseObject       = regexp.MustCompile(`^(?:这块|那块|这个|那个|一块|一个)?(?:(红色|蓝色|绿色|红|蓝|绿)的?)?(方块|积木|杯子|水杯|杯|瓶子|水瓶|瓶)$`)
	chineseLocation     = regexp.MustCompile(`^(?:(右侧|右边|右|左侧|左边|左))?(?:(红色|蓝色|绿色))?(收纳盒|收纳箱|箱子|箱|盒子|盒|交接区|交接点|交接位置|中间交接位置|目标区|垫子)(里面|里|内|上面|上)?$`)
	englishMove         = regexp.MustCompile(`(?i)^(?:put|place|move)\s+(.+?)\s+(?:in|into|to|on)\s+(.+)$`)
	englishFetch        = regexp.MustCompile(`(?i)^(?:bring|fetch|hand)\s+(?:me\s+)?(.+?)(?:\s+(?:here|to\s+me))?$`)
	englishObject       = regexp.MustCompile(`(?i)^(?:the\s+)?(?:(red|blue|green)\s+)?(cup|bottle|block)$`)
	englishLocation     = regexp.MustCompile(`(?i)^(?:the\s+)?(?:(right|left)\s+)?(?:(red|blue|green)\s+)?((?:storage\s+)?(?:bin|box)|handoff\s+(?:zone|point)|target\s+zone)$`)
	homeRouteVerb       = regexp.MustCompile(`(?:去|前往|到|巡检|巡查|检查|确认|回到|返回|从).*(?:客厅|卧室|卫生间|厕所|厨房|走廊|书房|阳台)`)
	homeRoomPattern     = regexp.MustCompile(`客厅|卧室|卫生间|厕所|厨房|走廊`)
)

func clarification(reason string) error {
	return fmt.Errorf("%s: %w", reason, ErrClarificationRequired)
}

// ValidateRequest runs before both parsing paths. This task grammar cannot turn
// conditional, negative, or cancellation requests into unconditional motion.
func ValidateRequest(request string) error {
	if nonAffirmative.MatchString(request) {
		return clarification("这句话包含否定、停止或条件要求，未启动动作；请通过取消按钮停止任务，或明确描述要执行的动作")
	}
	return nil
}

// ParseDestinationRevision resolves only a complete destination-only update.
// The task service supplies the existing object and robot; extra actions or
// unresolved constraints must never be discarded by that context fallback.
func ParseDestinationRevision(request string) (manipulation.EntitySelector, error) {
	if err := ValidateRequest(request); err != nil {
		return manipulation.EntitySelector{}, err
	}
	clause := normalizeRequest(request)
	for _, prefix := range []string{"最后", "最终"} {
		clause = strings.TrimSpace(strings.TrimPrefix(clause, prefix))
	}
	matches := chineseBareMove.FindStringSubmatch(clause)
	if len(matches) != 2 {
		return manipulation.EntitySelector{}, clarification("未能完整理解修改内容，请明确新的动作；只修改终点时可说“最后放到右侧蓝色垫子上”")
	}
	destination, ok := parseLocation(matches[1], true)
	if !ok || destination.Category != manipulation.CategoryTargetZone {
		return manipulation.EntitySelector{}, clarification("终点修改需要明确目标区或垫子，其他动作请完整描述")
	}
	return destination, nil
}

func (p *DeterministicParser) Parse(request string) (manipulation.Intent, error) {
	if err := ValidateRequest(request); err != nil {
		return manipulation.Intent{}, err
	}
	if home, handled, err := parseHomeRoute(request); handled {
		return home, err
	}
	segments := splitSequence(request)
	if len(segments) == 0 {
		return manipulation.Intent{}, ErrUnsupportedIntent
	}
	parsed := make([]manipulation.Intent, 0, len(segments))
	for _, segment := range segments {
		var previous *manipulation.Intent
		if len(parsed) > 0 {
			previous = &parsed[len(parsed)-1]
		}
		current, err := p.parseClause(segment, previous)
		if err != nil {
			if len(segments) > 1 {
				return manipulation.Intent{}, clarification("未能完整理解所有步骤，请逐步说明机器人、物体和目标位置")
			}
			return manipulation.Intent{}, err
		}
		parsed = append(parsed, current)
	}
	if len(parsed) == 1 {
		return parsed[0], nil
	}
	return sequenceIntent(parsed), nil
}

func parseHomeRoute(request string) (manipulation.Intent, bool, error) {
	normalized := normalizeRequest(request)
	if !homeRouteVerb.MatchString(normalized) {
		return manipulation.Intent{}, false, nil
	}
	matches := homeRoomPattern.FindAllString(normalized, -1)
	if len(matches) == 0 {
		return manipulation.Intent{}, true, clarification("家庭路线只支持客厅、走廊、厨房、卧室和卫生间，请明确要去的房间")
	}
	rooms := make([]string, 0, len(matches))
	for _, room := range matches {
		mapped := map[string]string{"客厅": "living_room", "走廊": "home_corridor", "厨房": "kitchen", "卧室": "bedroom", "卫生间": "bathroom", "厕所": "bathroom"}[room]
		if mapped == "" {
			return manipulation.Intent{}, true, clarification("暂不支持这个家庭房间，请使用客厅、走廊、厨房、卧室或卫生间")
		}
		rooms = append(rooms, mapped)
	}
	if len(rooms) < 2 {
		return manipulation.Intent{}, true, clarification("家庭路线至少需要起点和一个目标房间")
	}
	return manipulation.Intent{
		Action: manipulation.ActionHomeRoute, RouteRooms: rooms,
		ReturnToStart: strings.Contains(normalized, "回到") || strings.Contains(normalized, "返回") || strings.Contains(normalized, "回客厅"),
		Constraints:   manipulation.Constraints{KeepUpright: true, AvoidHumans: true},
	}, true, nil
}
func normalizeRequest(request string) string {
	request = strings.TrimSpace(strings.TrimRight(strings.TrimSpace(request), "。.!！"))
	for {
		before := request
		for _, prefix := range []string{"请帮我", "请帮忙", "麻烦", "帮我", "请", "先"} {
			request = strings.TrimSpace(strings.TrimPrefix(request, prefix))
		}
		if strings.HasPrefix(strings.ToLower(request), "please ") {
			request = strings.TrimSpace(request[len("please "):])
		}
		if request == before {
			return request
		}
	}
}
func extractRobotID(request string) (string, string) {
	request = normalizeRequest(request)
	for _, pattern := range []*regexp.Regexp{robotChinese, robotChineseReverse, robotEnglish} {
		if matches := pattern.FindStringSubmatch(request); len(matches) == 2 {
			number := matches[1]
			if n := map[string]string{"一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}[number]; n != "" {
				number = n
			}
			return "robot-" + number, normalizeRequest(request[len(matches[0]):])
		}
	}
	for _, prefix := range []string{"让xlerobot", "让机器人"} {
		request = strings.TrimSpace(strings.TrimPrefix(request, prefix))
	}
	return "", request
}
func (p *DeterministicParser) parseClause(request string, previous *manipulation.Intent) (manipulation.Intent, error) {
	robotID, clause := extractRobotID(request)
	clause = strings.TrimSpace(clause)
	objectText, sourceText, destinationText := "", "", ""
	action := manipulation.ActionPickAndPlace
	chinese := true
	switch {
	case chineseFromMove.MatchString(clause):
		m := chineseFromMove.FindStringSubmatch(clause)
		sourceText, objectText, destinationText = m[1], m[2], m[3]
	case chineseMove.MatchString(clause):
		m := chineseMove.FindStringSubmatch(clause)
		objectText, destinationText = m[1], m[2]
		if index := strings.Index(objectText, "从"); index >= 0 {
			objectText, sourceText = objectText[:index], objectText[index+len("从"):]
		}
	case chineseBareMove.MatchString(clause):
		objectText, destinationText = "它", chineseBareMove.FindStringSubmatch(clause)[1]
	case chineseFetch.MatchString(clause):
		objectText, action = chineseFetch.FindStringSubmatch(clause)[1], manipulation.ActionFetch
	case englishMove.MatchString(clause):
		m := englishMove.FindStringSubmatch(clause)
		objectText, destinationText, chinese = m[1], m[2], false
	case englishFetch.MatchString(clause):
		objectText, action, chinese = englishFetch.FindStringSubmatch(clause)[1], manipulation.ActionFetch, false
	default:
		if robotID != "" || strings.HasPrefix(clause, "把") || strings.HasPrefix(clause, "将") || strings.HasPrefix(clause, "从") {
			return manipulation.Intent{}, clarification("未能完整理解动作，请说明要移动的物体和目标位置")
		}
		return manipulation.Intent{}, ErrUnsupportedIntent
	}
	objectText = strings.TrimSpace(objectText)
	current := manipulation.Intent{Action: action, RobotID: robotID, Constraints: manipulation.Constraints{KeepUpright: true, AvoidHumans: true}}
	if objectText == "它" || strings.EqualFold(objectText, "it") {
		if previous == nil {
			return manipulation.Intent{}, clarification("还不知道“它”指什么，请先说出物体名称")
		}
		current.Object = cloneSelector(previous.Object)
		if action == manipulation.ActionPickAndPlace && sourceText == "" {
			current.Source = cloneSelector(previous.Destination)
		}
		if current.RobotID == "" {
			current.RobotID = previous.RobotID
		}
	} else {
		pattern := chineseObject
		if !chinese {
			pattern = englishObject
		}
		m := pattern.FindStringSubmatch(objectText)
		if len(m) != 3 {
			return manipulation.Intent{}, clarification("物体名称不明确或包含多个物体，请每一步描述一个杯子、瓶子或方块")
		}
		category := strings.ToLower(m[2])
		if chinese {
			category = chineseCategory(m[2])
		}
		current.Object = manipulation.EntitySelector{Category: category, Attributes: map[string]string{"color": normalizeColor(m[1])}}
	}
	if sourceText != "" {
		source, ok := parseLocation(sourceText, chinese)
		if !ok {
			return manipulation.Intent{}, clarification("起始位置尚不支持，请明确交接区、目标区或收纳盒")
		}
		current.Source = source
	}
	if action == manipulation.ActionFetch {
		current.Destination = manipulation.EntitySelector{Category: manipulation.CategoryDeliveryTray, Relation: "front_side"}
		return current, nil
	}
	destination, ok := parseLocation(destinationText, chinese)
	if !ok {
		return manipulation.Intent{}, clarification("目标位置不明确或尚不支持，请明确交接区、目标区或收纳盒；尚不支持冰箱等家电操作")
	}
	// Putting something ON a box is not the existing put-IN-container skill.
	if destination.Category == manipulation.CategoryStorageBin && ((chinese && strings.HasSuffix(destinationText, "上")) || (chinese && strings.HasSuffix(destinationText, "上面")) || (!chinese && strings.Contains(strings.ToLower(clause), " on "))) {
		return manipulation.Intent{}, clarification("当前收纳动作是放入容器，尚不支持放在容器顶部")
	}
	current.Destination = destination
	return current, nil
}
func cloneSelector(selector manipulation.EntitySelector) manipulation.EntitySelector {
	copy := selector
	if selector.Attributes != nil {
		copy.Attributes = make(map[string]string, len(selector.Attributes))
		for key, value := range selector.Attributes {
			copy.Attributes[key] = value
		}
	}
	return copy
}
func parseLocation(text string, chinese bool) (manipulation.EntitySelector, bool) {
	pattern := chineseLocation
	if !chinese {
		pattern = englishLocation
	}
	m := pattern.FindStringSubmatch(strings.TrimSpace(text))
	if len(m) < 4 {
		return manipulation.EntitySelector{}, false
	}
	category := manipulation.CategoryStorageBin
	name := strings.ToLower(strings.Join(strings.Fields(m[3]), " "))
	switch name {
	case "交接区", "交接点", "交接位置", "中间交接位置", "handoff zone", "handoff point":
		category = manipulation.CategoryHandoffZone
	case "目标区", "垫子", "target zone":
		category = manipulation.CategoryTargetZone
	}
	side := strings.ToLower(m[1])
	relation := ""
	if side == "right" || strings.HasPrefix(side, "右") {
		relation = "right_side"
	}
	if side == "left" || strings.HasPrefix(side, "左") {
		relation = "left_side"
	}
	selector := manipulation.EntitySelector{Category: category, Relation: relation}
	if m[2] != "" {
		selector.Attributes = map[string]string{"color": normalizeColor(m[2])}
	}
	return selector, true
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
func chineseCategory(text string) string {
	switch text {
	case "方块", "积木":
		return "block"
	case "瓶子", "水瓶", "瓶":
		return "bottle"
	case "杯子", "水杯", "杯":
		return "cup"
	}
	return ""
}
func normalizeColor(text string) string {
	switch text {
	case "红色", "红":
		return "red"
	case "蓝色", "蓝":
		return "blue"
	case "绿色", "绿":
		return "green"
	}
	return strings.ToLower(text)
}
