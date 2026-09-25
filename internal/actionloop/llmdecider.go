package actionloop

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
)

// The model-backed decider: one call, one choice.
//
// # Why the choice is expressed as a tool call
//
// The three ways a round can end — call a tool, declare the goal met, or declare
// that it cannot be done — are all expressed in the same structured channel the
// model already uses for tool calls. Asking for them in prose and parsing the
// reply would mean deciding, from a sentence, whether the model chose something or
// merely mentioned it; and "I cannot proceed" is the answer a person most needs to
// receive verbatim, not reconstructed.
//
// So the request advertises the real tools plus two control tools, and the
// decider maps the control calls onto `Done` and `Blocked`. The control tools are
// never in `Loop.Tools`: the loop cannot run them, because they are not actions.
//
// # What the model is told about the harness
//
// The rules that will be enforced are stated in the prompt — that a successful
// return is not completion, that a physical call needs approval, that an unknown
// outcome ends everything. That is not decoration: a model that does not know the
// rules chooses things that will be refused, and the rounds spent discovering them
// are the operator's time.

// Control tool names. They are reserved: a real tool may not take either name,
// because the decider could not then tell a decision from an action.
const (
	// ControlFinishTool is how the model says the goal is met.
	ControlFinishTool = "finish"
	// ControlBlockedTool is how the model says it cannot proceed.
	ControlBlockedTool = "cannot_proceed"
)

// ErrReservedToolName means a real tool claimed one of the control names.
var ErrReservedToolName = errors.New("a tool may not use a reserved control name")

// LLMDecider chooses by asking a model.
type LLMDecider struct {
	// BaseURL is an OpenAI-compatible endpoint, without the trailing path.
	BaseURL string
	// APIKey is sent as a bearer token when non-empty.
	APIKey string
	// Model is the model name.
	Model string
	// Timeout bounds one decision. Zero means DefaultDecisionTimeout.
	Timeout time.Duration
	// Client, when set, replaces the default HTTP client. Tests use it.
	Client *http.Client
}

// DefaultDecisionTimeout bounds one round's model call.
//
// It is shorter than a planning call's budget on purpose: a decision is about
// what to do next with what is already known, and a model that needs thirty
// seconds to answer that is a model that is not going to drive a robot.
const DefaultDecisionTimeout = 20 * time.Second

// Decide asks the model for one round.
func (d *LLMDecider) Decide(ctx context.Context, request Request) (Decision, error) {
	if strings.TrimSpace(d.BaseURL) == "" {
		return Decision{}, errors.New("the decider has no model endpoint")
	}
	if strings.TrimSpace(d.Model) == "" {
		return Decision{}, errors.New("the decider has no model name")
	}
	for _, tool := range request.Tools {
		if tool.Name == ControlFinishTool || tool.Name == ControlBlockedTool {
			return Decision{}, fmt.Errorf("%w: %s", ErrReservedToolName, tool.Name)
		}
	}

	if agentcontext.Mode() != "legacy" && request.ContextSnapshot == nil {
		snapshot, err := SnapshotFor(request)
		if err != nil {
			return Decision{}, err
		}
		request.ContextSnapshot = &snapshot
	}
	wireTools, toolNames, err := modelToolSchemas(request.Tools)
	if err != nil {
		return Decision{}, err
	}
	body, err := json.Marshal(chatRequest{
		Model:    d.Model,
		Messages: messages(request),
		Tools:    wireTools,
		// Some thinking models reject required. The response parser still
		// requires exactly one offered call; prose never authorizes an action.
		ToolChoice: "auto",
		// One choice per round. The loop is what sequences them, and a model that
		// returned three calls would have decided an order the harness never
		// validated.
		ParallelToolCalls: false,
	})
	if err != nil {
		return Decision{}, err
	}

	timeout := d.Timeout
	if timeout <= 0 {
		timeout = DefaultDecisionTimeout
	}
	callCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	httpRequest, err := http.NewRequestWithContext(
		callCtx, http.MethodPost, strings.TrimRight(d.BaseURL, "/")+"/chat/completions",
		bytes.NewReader(body))
	if err != nil {
		return Decision{}, err
	}
	httpRequest.Header.Set("Content-Type", "application/json")
	if d.APIKey != "" {
		httpRequest.Header.Set("Authorization", "Bearer "+d.APIKey)
	}
	client := d.Client
	if client == nil {
		client = http.DefaultClient
	}
	response, err := client.Do(httpRequest)
	if err != nil {
		return Decision{}, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		detail, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return Decision{}, fmt.Errorf("model returned %d: %s", response.StatusCode, strings.TrimSpace(string(detail)))
	}
	const maxModelResponse = 1 << 20
	result, err := io.ReadAll(io.LimitReader(response.Body, maxModelResponse+1))
	if err != nil {
		return Decision{}, err
	}
	if len(result) > maxModelResponse {
		return Decision{}, errors.New("model response exceeds 1 MiB")
	}
	var completion chatResponse
	if err := json.Unmarshal(result, &completion); err != nil {
		return Decision{}, err
	}
	if len(completion.Choices) == 0 {
		return Decision{}, errors.New("the model returned no choices")
	}
	message := completion.Choices[0].Message
	for i := range message.ToolCalls {
		wireName := message.ToolCalls[i].Function.Name
		original, offered := toolNames[wireName]
		if !offered {
			return Decision{}, fmt.Errorf("model chose a tool not offered in this round: %s", wireName)
		}
		message.ToolCalls[i].Function.Name = original
	}
	return decisionFrom(message)
}

// decisionFrom turns the model's message into a decision.
//
// A message with no tool call is refused rather than read as an answer. The
// harness requires a tool call, so a reply without one means the model
// did not answer the question — and guessing "it probably meant to stop" would put
// a decision in the record that the model never made.
func decisionFrom(message chatMessage) (Decision, error) {
	if len(message.ToolCalls) == 0 {
		content := strings.TrimSpace(message.Content)
		if content == "" {
			return Decision{}, errors.New("the model returned neither a tool call nor text")
		}
		return Decision{}, fmt.Errorf("the model did not choose: %.200s", content)
	}
	if len(message.ToolCalls) != 1 {
		return Decision{}, errors.New("the model must choose exactly one tool per round")
	}
	call := message.ToolCalls[0]
	var arguments map[string]any
	if strings.TrimSpace(call.Function.Arguments) != "" {
		if err := json.Unmarshal([]byte(call.Function.Arguments), &arguments); err != nil {
			return Decision{}, fmt.Errorf("the arguments for %s are not JSON: %w", call.Function.Name, err)
		}
	}
	switch call.Function.Name {
	case ControlFinishTool:
		reason, _ := arguments["reason"].(string)
		return Decision{Done: true, Reason: reason}, nil
	case ControlBlockedTool:
		reason, _ := arguments["reason"].(string)
		if strings.TrimSpace(reason) == "" {
			reason = "模型表示无法继续，但没有说明原因"
		}
		return Decision{Blocked: reason}, nil
	}
	reason, _ := arguments["reason"].(string)
	delete(arguments, "reason")
	return Decision{Tool: call.Function.Name, Arguments: arguments, Reason: reason}, nil
}

// messages builds the conversation the model sees.
func messages(request Request) []chatMessage {
	if request.ContextSnapshot != nil {
		return []chatMessage{{Role: "system", Content: systemPrompt(request)}, {Role: "user", Content: request.ContextSnapshot.Text}}
	}
	conversation := []chatMessage{{Role: "system", Content: systemPrompt(request)}}
	for _, round := range request.History {
		// The history is the record, restated in the model's own channel: what it
		// chose, what the harness said about it, and what the tool reported. A model
		// that could not see the verdict would choose the same refused call again.
		summary := fmt.Sprintf("第 %d 轮：%s → %s", round.Round, describeRound(round), round.Verdict)
		if round.Detail != "" {
			summary += "（" + round.Detail + "）"
		}
		if round.ToolMessage != "" {
			summary += "；工具回执：" + round.ToolMessage
		}
		if len(round.ResultDetail) > 0 {
			encoded, _ := json.Marshal(round.ResultDetail)
			if len(encoded) > 8192 {
				encoded = encoded[:8192]
			}
			summary += "；工具数据：" + string(encoded)
		}
		conversation = append(conversation, chatMessage{Role: "user", Content: summary})
	}
	conversation = append(conversation, chatMessage{
		Role:    "user",
		Content: fmt.Sprintf("当前观测：%s\n请选择下一步。", request.Observation.Summary),
	})
	return conversation
}

func describeRound(round Round) string {
	if round.Tool == "" {
		return "无工具调用"
	}
	if round.Reason == "" {
		return round.Tool
	}
	return round.Tool + "（" + round.Reason + "）"
}

// systemPrompt states the goal and the rules that will be enforced.
func systemPrompt(request Request) string {
	var builder strings.Builder
	if request.Role == "system" {
		builder.WriteString("你是机群服务器的系统任务 Agent。每一轮只能调用一个已提供的 Fleet 工具、报告分析或提案已完成，或说明无法继续。任务草案仍需独立操作员审批，绝不能把模型文字当成机器人动作授权。\n\n")
	} else {
		builder.WriteString("你是一台家用机器人的执行决策器。每一轮你只能做一件事：调用一个工具、宣告任务完成、或者说明你无法继续。\n\n")
	}
	builder.WriteString("目标：" + request.Goal + "\n\n")
	builder.WriteString("规则（由 harness 强制执行，不是建议）：\n")
	builder.WriteString("- 工具返回成功不等于完成：改变世界的动作必须有动作之后的新鲜观测才能确认。\n")
	builder.WriteString("- 物理动作需要人工批准；没有被批准的物理动作不会执行，也不会换一个工具绕过去。\n")
	builder.WriteString("- 一旦某个动作的结果未知（可能已经动了，但无法确认），一切立即停止，不允许重试或换工具。\n")
	builder.WriteString("- 同一类失败重复出现时不要继续尝试；无法完成就用 cannot_proceed 说明原因，交给人。\n")
	builder.WriteString("- 只从下面的工具里选，不要发明工具名。\n\n")
	builder.WriteString("调用工具时，arguments 里额外带一个 reason 字段说明为什么选它，这一条会被记录下来供人复核。\n")
	return builder.String()
}

// toolSchemas renders the offered tools plus the two control tools.
func toolSchemas(tools []Tool) []toolSchema {
	schemas := make([]toolSchema, 0, len(tools)+2)
	for _, tool := range tools {
		schemas = append(schemas, toolSchema{
			Type: "function",
			Function: toolFunction{
				Name: tool.Name, Description: tool.Description,
				Parameters: parametersSchema(tool.Parameters),
			},
		})
	}
	schemas = append(schemas,
		toolSchema{Type: "function", Function: toolFunction{
			Name: ControlFinishTool,
			Description: "目标已经达成。只有在确认世界已经处于目标状态时才调用。" +
				"如果还有改变世界的动作没有通过证据确认，不要调用它。",
			Parameters: parametersSchema([]string{"reason"}),
		}},
		toolSchema{Type: "function", Function: toolFunction{
			Name:        ControlBlockedTool,
			Description: "你无法继续（缺少条件、需要人、或反复失败）。说明原因，交给人处理。",
			Parameters:  parametersSchema([]string{"reason"}),
		}},
	)
	return schemas
}

var modelFunctionName = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,64}$`)

// Internal capability names contain dots, while model function names cannot.
// Reserve every original name before allocating aliases so two distinct tools
// cannot collapse onto the same wire name. Decode only this round's allowlist.
func modelToolSchemas(tools []Tool) ([]toolSchema, map[string]string, error) {
	schemas := toolSchemas(tools)
	used := map[string]bool{}
	for _, schema := range schemas {
		name := schema.Function.Name
		if used[name] || strings.TrimSpace(name) == "" {
			return nil, nil, fmt.Errorf("duplicate or empty tool name: %q", name)
		}
		used[name] = true
	}
	names := map[string]string{}
	next := 0
	for i := range schemas {
		original := schemas[i].Function.Name
		wire := original
		if !modelFunctionName.MatchString(wire) {
			for {
				wire = fmt.Sprintf("tool_%d", next)
				next++
				if !used[wire] {
					break
				}
			}
			used[wire] = true
			schemas[i].Function.Name = wire
			schemas[i].Function.Description = "系统工具：" + original + "。" + schemas[i].Function.Description
		}
		names[wire] = original
	}
	return schemas, names, nil
}

func parametersSchema(names []string) map[string]any {
	properties := map[string]any{}
	for _, name := range names {
		properties[name] = map[string]any{"type": "string"}
	}
	// `reason` is accepted on every tool so the model can always explain itself,
	// and it is stripped before the arguments reach the tool.
	properties["reason"] = map[string]any{
		"type":        "string",
		"description": "为什么选这个工具，会记录到决策账里",
	}
	return map[string]any{"type": "object", "properties": properties}
}

// Wire shapes. They mirror the OpenAI chat-completions format, which every
// OpenAI-compatible endpoint this repository talks to also speaks.
type chatRequest struct {
	Model             string        `json:"model"`
	Messages          []chatMessage `json:"messages"`
	Tools             []toolSchema  `json:"tools,omitempty"`
	ToolChoice        string        `json:"tool_choice,omitempty"`
	ParallelToolCalls bool          `json:"parallel_tool_calls"`
}

type chatMessage struct {
	Role      string     `json:"role"`
	Content   string     `json:"content,omitempty"`
	ToolCalls []toolCall `json:"tool_calls,omitempty"`
}

type toolCall struct {
	Function toolCallFunction `json:"function"`
}

type toolCallFunction struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

type toolSchema struct {
	Type     string       `json:"type"`
	Function toolFunction `json:"function"`
}

type toolFunction struct {
	Name        string         `json:"name"`
	Description string         `json:"description,omitempty"`
	Parameters  map[string]any `json:"parameters,omitempty"`
}

type chatResponse struct {
	Choices []struct {
		Message chatMessage `json:"message"`
	} `json:"choices"`
}
