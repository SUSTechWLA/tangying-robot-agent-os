// Package agent provides the first version of the Tangying task agent.
//
// The agent is intentionally small: it turns natural language into a
// manipulation.Intent. A deterministic parser is always the safety net; when
// an OpenAI-compatible endpoint is configured the agent first asks the model
// to select a tool (pick_and_place or fetch) and falls back to deterministic
// parsing if the model is unavailable or returns an invalid plan.
package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

const (
	ProviderDeterministic = "deterministic"
	ProviderOpenAI        = "openai"
)

type Config struct {
	Provider   string
	BaseURL    string
	APIKey     string
	Model      string
	HTTPClient *http.Client
}

// Parser implements intent.Parser and can therefore be dropped into the
// existing orchestrator and API without changing task persistence.
type Parser struct {
	deterministic *intent.DeterministicParser
	llm           *llmPlanner
}

func NewParser(config Config) intent.Parser {
	parser := &Parser{deterministic: intent.NewDeterministicParser()}
	if strings.EqualFold(config.Provider, ProviderOpenAI) && config.BaseURL != "" && config.APIKey != "" && config.Model != "" {
		parser.llm = newLLMPlanner(config)
	}
	return parser
}

func (p *Parser) Parse(request string) (manipulation.Intent, error) {
	if p.llm != nil {
		if parsed, err := p.llm.Plan(request); err == nil {
			return parsed, nil
		}
	}
	return p.deterministic.Parse(request)
}

type llmPlanner struct {
	baseURL string
	apiKey  string
	model   string
	client  *http.Client
}

func newLLMPlanner(config Config) *llmPlanner {
	client := config.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: 20 * time.Second}
	}
	return &llmPlanner{
		baseURL: strings.TrimRight(config.BaseURL, "/"),
		apiKey:  config.APIKey,
		model:   config.Model,
		client:  client,
	}
}

func (p *llmPlanner) Plan(request string) (manipulation.Intent, error) {
	body, err := json.Marshal(chatCompletionRequest{
		Model: p.model,
		Messages: []chatMessage{
			{Role: "system", Content: systemPrompt},
			{Role: "user", Content: request},
		},
		Tools:      manipulationTools(),
		ToolChoice: "auto",
	})
	if err != nil {
		return manipulation.Intent{}, err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, p.baseURL+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return manipulation.Intent{}, err
	}
	httpRequest.Header.Set("Content-Type", "application/json")
	httpRequest.Header.Set("Authorization", "Bearer "+p.apiKey)

	response, err := p.client.Do(httpRequest)
	if err != nil {
		return manipulation.Intent{}, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		message, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return manipulation.Intent{}, fmt.Errorf("llm status %d: %s", response.StatusCode, strings.TrimSpace(string(message)))
	}

	var completion chatCompletionResponse
	if err := json.NewDecoder(response.Body).Decode(&completion); err != nil {
		return manipulation.Intent{}, err
	}
	if len(completion.Choices) == 0 {
		return manipulation.Intent{}, errors.New("llm returned no choices")
	}
	choice := completion.Choices[0].Message
	if len(choice.ToolCalls) == 0 {
		return parseIntentFromContent(choice.Content)
	}
	parsed := make([]manipulation.Intent, 0, len(choice.ToolCalls))
	for _, toolCall := range choice.ToolCalls {
		intent, err := parseToolCall(toolCall.Function.Name, toolCall.Function.Arguments)
		if err != nil {
			return manipulation.Intent{}, err
		}
		parsed = append(parsed, intent)
	}
	return combineIntents(parsed), nil
}

func combineIntents(parsed []manipulation.Intent) manipulation.Intent {
	first := parsed[0]
	if len(parsed) > 1 {
		first.Sequence = append([]manipulation.Intent(nil), parsed...)
	}
	return first
}

type chatCompletionRequest struct {
	Model      string        `json:"model"`
	Messages   []chatMessage `json:"messages"`
	Tools      []tool        `json:"tools"`
	ToolChoice string        `json:"tool_choice,omitempty"`
}

type chatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content,omitempty"`
}

type tool struct {
	Type     string       `json:"type"`
	Function toolFunction `json:"function"`
}

type toolFunction struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Parameters  map[string]any `json:"parameters"`
}

type chatCompletionResponse struct {
	Choices []struct {
		Message struct {
			Content   string `json:"content"`
			ToolCalls []struct {
				Function struct {
					Name      string `json:"name"`
					Arguments string `json:"arguments"`
				} `json:"function"`
			} `json:"tool_calls"`
		} `json:"message"`
	} `json:"choices"`
}

const systemPrompt = `You control a tabletop robot. Choose one or more ordered tool calls and return only JSON arguments.
Supported objects are cups, bottles and blocks with optional colors red, blue or green.
pick_and_place puts an object into a storage_bin on the right_side or left_side.
fetch brings an object to the front delivery_tray for the user.
For a compound request such as "put A away, then bring me B", return the tool calls in execution order.`

func manipulationTools() []tool {
	return []tool{
		{
			Type: "function",
			Function: toolFunction{
				Name:        "pick_and_place",
				Description: "Pick up an object and place it into a storage bin.",
				Parameters: map[string]any{
					"type": "object",
					"properties": map[string]any{
						"object": map[string]any{
							"type": "object",
							"properties": map[string]any{
								"category": map[string]any{"type": "string", "enum": []string{"cup", "bottle", "block"}},
								"color":    map[string]any{"type": "string", "enum": []string{"red", "blue", "green"}},
							},
							"required": []string{"category", "color"},
						},
						"destination_relation": map[string]any{"type": "string", "enum": []string{"right_side", "left_side"}},
					},
					"required": []string{"object", "destination_relation"},
				},
			},
		},
		{
			Type: "function",
			Function: toolFunction{
				Name:        "fetch",
				Description: "Pick up an object and bring it to the front delivery tray for the user.",
				Parameters: map[string]any{
					"type": "object",
					"properties": map[string]any{
						"object": map[string]any{
							"type": "object",
							"properties": map[string]any{
								"category": map[string]any{"type": "string", "enum": []string{"cup", "bottle", "block"}},
								"color":    map[string]any{"type": "string", "enum": []string{"red", "blue", "green"}},
							},
							"required": []string{"category", "color"},
						},
					},
					"required": []string{"object"},
				},
			},
		},
	}
}

type llmIntent struct {
	Action              string         `json:"action"`
	Object              llmSelector    `json:"object"`
	Destination         llmSelector    `json:"destination,omitempty"`
	DestinationRelation string         `json:"destination_relation,omitempty"`
	Constraints         llmConstraints `json:"constraints,omitempty"`
}

type llmSelector struct {
	Category   string            `json:"category"`
	Color      string            `json:"color"`
	Attributes map[string]string `json:"attributes,omitempty"`
	Relation   string            `json:"relation,omitempty"`
}

type llmConstraints struct {
	KeepUpright bool `json:"keepUpright"`
	AvoidHumans bool `json:"avoidHumans"`
}

func parseToolCall(name, arguments string) (manipulation.Intent, error) {
	var parsed llmIntent
	if err := json.Unmarshal([]byte(arguments), &parsed); err != nil {
		return manipulation.Intent{}, err
	}
	parsed.Action = strings.TrimSpace(name)
	parsed.Object.Category = strings.ToLower(strings.TrimSpace(parsed.Object.Category))
	parsed.Object.Color = strings.ToLower(strings.TrimSpace(parsed.Object.Color))
	if parsed.DestinationRelation == "" {
		parsed.DestinationRelation = parsed.Destination.Relation
	}
	parsed.DestinationRelation = strings.ToLower(strings.TrimSpace(parsed.DestinationRelation))
	return normalizeIntent(parsed)
}

func parseIntentFromContent(content string) (manipulation.Intent, error) {
	content = strings.TrimSpace(content)
	start := strings.Index(content, "{")
	end := strings.LastIndex(content, "}")
	if start < 0 || end <= start {
		return manipulation.Intent{}, errors.New("llm content does not contain a JSON intent")
	}
	return parseIntent([]byte(content[start : end+1]))
}

func parseIntent(raw []byte) (manipulation.Intent, error) {
	var parsed llmIntent
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return manipulation.Intent{}, err
	}
	parsed.Action = strings.TrimSpace(parsed.Action)
	parsed.Object.Category = strings.ToLower(strings.TrimSpace(parsed.Object.Category))
	parsed.Object.Color = strings.ToLower(strings.TrimSpace(parsed.Object.Color))
	if parsed.DestinationRelation == "" {
		parsed.DestinationRelation = parsed.Destination.Relation
	}
	parsed.DestinationRelation = strings.ToLower(strings.TrimSpace(parsed.DestinationRelation))
	return normalizeIntent(parsed)
}

func normalizeIntent(parsed llmIntent) (manipulation.Intent, error) {
	if !validCategory(parsed.Object.Category) || !validColor(parsed.Object.Color) {
		return manipulation.Intent{}, fmt.Errorf("unsupported object %q %q", parsed.Object.Category, parsed.Object.Color)
	}
	switch parsed.Action {
	case manipulation.ActionPickAndPlace:
		if !validRelation(parsed.DestinationRelation) {
			return manipulation.Intent{}, fmt.Errorf("unsupported destination relation %q", parsed.DestinationRelation)
		}
		return manipulation.Intent{
			Action: parsed.Action,
			Object: manipulation.EntitySelector{
				Category:   parsed.Object.Category,
				Attributes: map[string]string{"color": parsed.Object.Color},
			},
			Destination: manipulation.EntitySelector{
				Category: manipulation.CategoryStorageBin,
				Relation: parsed.DestinationRelation,
			},
			Constraints: manipulation.Constraints{KeepUpright: true, AvoidHumans: true},
		}, nil
	case manipulation.ActionFetch:
		return manipulation.Intent{
			Action: parsed.Action,
			Object: manipulation.EntitySelector{
				Category:   parsed.Object.Category,
				Attributes: map[string]string{"color": parsed.Object.Color},
			},
			Destination: manipulation.EntitySelector{
				Category: manipulation.CategoryDeliveryTray,
				Relation: "front_side",
			},
			Constraints: manipulation.Constraints{KeepUpright: true, AvoidHumans: true},
		}, nil
	default:
		return manipulation.Intent{}, fmt.Errorf("unsupported action %q", parsed.Action)
	}
}

func validCategory(category string) bool {
	return category == "cup" || category == "bottle" || category == "block"
}

func validColor(color string) bool {
	return color == "red" || color == "blue" || color == "green"
}

func validRelation(relation string) bool {
	return relation == "right_side" || relation == "left_side"
}
