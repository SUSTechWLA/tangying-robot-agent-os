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
	// Colloquial speech points at a container with a demonstrative ("右边那个
	// 盒子") instead of the catalogue noun phrase ("右侧收纳盒"). Accept the
	// demonstrative without changing which containers are commissioned.
	chineseLocation  = regexp.MustCompile(`^(?:(右侧|右边|右|左侧|左边|左))?(?:(红色|蓝色|绿色))?(?:(?:那|这)(?:个|只|些|一个))?(收纳盒|收纳箱|箱子|箱|盒子|盒|交接区|交接点|交接位置|中间交接位置|目标区|垫子)(里面|里边|里头|里|内|上面|上)?$`)
	englishMove      = regexp.MustCompile(`(?i)^(?:put|place|move)\s+(.+?)\s+(?:in|into|to|on)\s+(.+)$`)
	englishFetch     = regexp.MustCompile(`(?i)^(?:bring|fetch|hand)\s+(?:me\s+)?(.+?)(?:\s+(?:here|to\s+me))?$`)
	englishObject    = regexp.MustCompile(`(?i)^(?:the\s+)?(?:(red|blue|green)\s+)?(cup|bottle|block)$`)
	englishLocation  = regexp.MustCompile(`(?i)^(?:the\s+)?(?:(right|left)\s+)?(?:(red|blue|green)\s+)?((?:storage\s+)?(?:bin|box)|handoff\s+(?:zone|point)|target\s+zone)$`)
	homeObjectAction = regexp.MustCompile(`(?:拿|取|抓|拾)(?:(红色|蓝色|绿色|黄色|红|蓝|绿|黄)的?)?(方块|积木|杯子|水杯|杯|瓶子|水瓶|瓶|盘子|碟子|碗)`)
	// Enumerating objects: "拿红色杯子和蓝色杯子". The second object has no verb of
	// its own, so a conjunction may stand in for one. Scoped to listing objects, so
	// the single-object grammar keeps requiring an explicit verb.
	homeObjectList        = regexp.MustCompile(`(?:拿|取|抓|拾|和|、|及|与|还有)(?:(红色|蓝色|绿色|黄色|红|蓝|绿|黄)的?)?(方块|积木|杯子|水杯|杯|瓶子|水瓶|瓶|盘子|碟子|碗)`)
	homeDestinationAction = regexp.MustCompile(`(?:放到|放进|放入|放在)(?:(右侧|右边|右|左侧|左边|左))?(?:(红色|蓝色|绿色))?(?:(?:那|这)(?:个|只|些|一个))?(收纳盒|收纳箱|箱子|箱|盒子|盒)`)
	homeRouteVerb         = regexp.MustCompile(`(?:去|前往|到|巡检|巡查|检查|确认|回到|返回|从).*(?:客厅|卧室|卫生间|厕所|厨房|走廊|书房|阳台)`)
	homeRoomPattern       = regexp.MustCompile(`客厅|卧室|卫生间|厕所|厨房|走廊`)
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
	if home, handled, err := parseHomeManipulation(request); handled {
		return home, err
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

// parseHomeManipulation recognizes the household transfer grammar before the
// route-only grammar. Room names remain planning metadata; the object and
// destination still have to be grounded from the destination RGB-D capture.
func parseHomeManipulation(request string) (manipulation.Intent, bool, error) {
	normalized := normalizeRequest(request)
	if !strings.Contains(normalized, "拿") && !strings.Contains(normalized, "取") &&
		!strings.Contains(normalized, "抓") && !strings.Contains(normalized, "拾") {
		return manipulation.Intent{}, false, nil
	}
	// Ordinary tabletop fetch/move requests also use 拿/取. Only claim the
	// household grammar when the sentence names at least one commissioned room;
	// otherwise the existing clause parser must handle it.
	if len(homeRoomPattern.FindAllString(normalized, -1)) == 0 {
		return manipulation.Intent{}, false, nil
	}
	if !strings.Contains(normalized, "放") {
		return manipulation.Intent{}, true, clarification("家庭抓取还需要明确放入哪个收纳盒")
	}
	rooms := make([]string, 0, 3)
	for _, room := range homeRoomPattern.FindAllString(normalized, -1) {
		mapped := map[string]string{"客厅": "living_room", "走廊": "home_corridor", "厨房": "kitchen", "卧室": "bedroom", "卫生间": "bathroom", "厕所": "bathroom"}[room]
		if mapped != "" {
			rooms = append(rooms, mapped)
		}
	}
	if len(rooms) < 2 {
		return manipulation.Intent{}, true, clarification("家庭抓取至少需要起点和目标房间")
	}
	transfers, err := homeTransfers(normalized)
	if err != nil {
		return manipulation.Intent{}, true, err
	}
	returnToStart := strings.Contains(normalized, "回到") || strings.Contains(normalized, "返回") ||
		strings.Contains(normalized, "回客厅")

	// Every transfer shares the route and the return instruction, which belong to
	// the request as a whole; only the first carries them so the planner does not
	// re-plan the same journey for each object.
	intents := make([]manipulation.Intent, 0, len(transfers))
	for index, transfer := range transfers {
		intent := manipulation.Intent{
			Action:      manipulation.ActionHomeManipulation,
			Object:      transfer.object,
			Destination: transfer.destination,
			Constraints: manipulation.Constraints{KeepUpright: true, AvoidHumans: true},
		}
		// Every transfer carries the route. Giving it only to the first looked like
		// an optimisation - one journey instead of several - but the planner builds
		// navigation steps from the intent's own RouteRooms, so the later transfers
		// planned nothing beyond the initial observation and the task reported success
		// after moving one object. Each transfer plans its own route now.
		intent.RouteRooms = append([]string(nil), rooms...)
		// Only the last transfer returns, or the robot would drive home in between.
		intent.ReturnToStart = returnToStart && index == len(transfers)-1
		intents = append(intents, intent)
	}
	if len(intents) == 1 {
		return intents[0], true, nil
	}
	return sequenceIntent(intents), true, nil
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

func homeDestination(dm []string) manipulation.EntitySelector {
	destination := manipulation.EntitySelector{Category: manipulation.CategoryStorageBin}
	if len(dm) > 2 && dm[2] != "" {
		destination.Attributes = map[string]string{"color": normalizeColor(dm[2])}
	}
	if len(dm) > 1 && dm[1] != "" {
		if strings.HasPrefix(dm[1], "右") {
			destination.Relation = "right_side"
		} else {
			destination.Relation = "left_side"
		}
	}
	return destination
}

// transfer is one object moved to one destination.
type transfer struct {
	object      manipulation.EntitySelector
	destination manipulation.EntitySelector
}

// homeTransfers reads every "拿X放进Y" pair from a household request.
//
// A request may describe several moves: separated by 再/然后/，, or listing objects
// together before a single destination ("拿红色杯子和蓝色杯子放进蓝色收纳盒"). Both
// forms are read here rather than matching the first pair and dropping the rest -
// moving one object while reporting success for the whole instruction is the worst
// outcome available, because nobody rechecks a task that says it succeeded.
func homeTransfers(normalized string) ([]transfer, error) {
	var transfers []transfer
	// An object and its destination are often in different clauses
	// ("拿红色杯子，放进蓝色收纳盒"). Objects are held until a clause supplies a
	// destination, instead of requiring both in the same clause.
	var pending []manipulation.EntitySelector
	for _, segment := range splitSequence(normalized) {
		objects := homeObjectList.FindAllStringSubmatch(segment, -1)
		destinations := homeDestinationAction.FindAllStringSubmatch(segment, -1)
		if len(objects) == 0 && len(destinations) == 0 {
			continue
		}
		if len(objects) == 0 && len(pending) > 0 {
			// This clause only says where to put what came before.
			for _, selector := range pending {
				transfers = append(transfers, transfer{
					object:      selector,
					destination: homeDestination(destinations[0]),
				})
			}
			pending = nil
			continue
		}
		if len(objects) == 0 {
			// A destination with nothing to put in it. Silently dropping it would
			// leave part of the instruction unexecuted, which is the failure this
			// whole function exists to prevent.
			return nil, clarification("有一个目标位置没有对应的物体，请说明要把什么放进去")
		}
		selectors := make([]manipulation.EntitySelector, 0, len(objects))
		for _, om := range objects {
			category := chineseCategory(om[2])
			if category == "" {
				return nil, clarification("家庭抓取物体不明确，当前场景请说出红色杯子、蓝色杯子、绿色杯子或黄色盘子")
			}
			selectors = append(selectors, manipulation.EntitySelector{
				Category:   category,
				Attributes: map[string]string{"color": normalizeColor(om[1])},
			})
		}
		if len(destinations) == 0 {
			pending = append(pending, selectors...)
			continue
		}
		if len(destinations) > 1 && len(destinations) != len(selectors) {
			return nil, clarification("请为每个物体分别说明放在哪里")
		}
		group := append(append([]manipulation.EntitySelector(nil), pending...), selectors...)
		pending = nil
		for index, selector := range group {
			// One destination serves every object in the clause, which is what
			// "把A和B都放进C" means; several destinations pair up in order.
			dm := destinations[0]
			if len(destinations) == len(group) {
				dm = destinations[index]
			}
			transfers = append(transfers, transfer{
				object: selector, destination: homeDestination(dm),
			})
		}
	}
	if len(pending) > 0 {
		return nil, clarification("家庭抓取目标不明确，当前场景请说出蓝色收纳盒")
	}
	if len(transfers) == 0 {
		return nil, clarification("家庭抓取物体不明确，当前场景请说出红色杯子、蓝色杯子、绿色杯子或黄色盘子")
	}
	return transfers, nil
}

func chineseCategory(text string) string {
	switch text {
	case "方块", "积木":
		return "block"
	case "瓶子", "水瓶", "瓶":
		return "bottle"
	case "杯子", "水杯", "杯":
		return "cup"
	case "盘子", "碟子":
		return "plate"
	case "碗":
		return "bowl"
	}
	return ""
}
func normalizeColor(text string) string {
	switch text {
	case "红色", "红":
		return "red"
	case "蓝色", "蓝":
		return "blue"
	case "黄色", "黄":
		return "yellow"
	case "绿色", "绿":
		return "green"
	}
	return strings.ToLower(text)
}
