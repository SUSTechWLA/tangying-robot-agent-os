package orchestration

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// Config controls LLM planning. Provider/BaseURL/APIKey/Model mirror the
// Local Agent settings; Samples > 1 enables self-consistency voting.
type Config struct {
	Provider   string
	BaseURL    string
	APIKey     string
	Model      string
	HTTPClient *http.Client
	Samples    int
}

// New returns an LLM planner when an OpenAI-compatible endpoint is fully
// configured, otherwise the deterministic fallback planner.
func New(catalog []skills.SkillManifest, config Config) Planner {
	if !strings.EqualFold(config.Provider, "openai") || config.BaseURL == "" || config.APIKey == "" || config.Model == "" {
		return DeterministicPlanner{}
	}
	samples := config.Samples
	if samples <= 0 {
		samples = 1
	}
	if samples > 5 {
		samples = 5
	}
	client := config.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: 30 * time.Second}
	}
	return &LLMPlanner{
		catalog: catalog,
		baseURL: strings.TrimRight(config.BaseURL, "/"),
		apiKey:  config.APIKey,
		model:   config.Model,
		client:  client,
		samples: samples,
	}
}

type LLMPlanner struct {
	catalog []skills.SkillManifest
	baseURL string
	apiKey  string
	model   string
	client  *http.Client
	samples int
}

type candidate struct {
	bundle Bundle
	hash   string
}

func (p *LLMPlanner) Plan(request string, intent manipulation.Intent) (Bundle, error) {
	intents := intent.Tasks()
	attempts := 0
	var candidates []candidate
	var rejections []string
	for sample := 0; sample < p.samples; sample++ {
		attempts++
		body, err := json.Marshal(chatCompletionRequest{
			Model: p.model,
			Messages: []chatMessage{
				{Role: "system", Content: p.systemPrompt(intents)},
				{Role: "user", Content: request},
			},
		})
		if err != nil {
			rejections = append(rejections, fmt.Sprintf("marshal request: %v", err))
			continue
		}
		completion, err := p.post(body)
		if err != nil {
			rejections = append(rejections, err.Error())
			continue
		}
		bundle, err := parseBundle(completion)
		if err != nil {
			rejections = append(rejections, err.Error())
			continue
		}
		if err := validateBundle(bundle, intents, p.catalog); err != nil {
			rejections = append(rejections, err.Error())
			continue
		}
		candidates = append(candidates, candidate{bundle: bundle, hash: hashBundle(bundle)})
	}

	if len(candidates) == 0 {
		return Bundle{
			Source:     SourceDeterministic,
			Attempts:   attempts,
			Rejections: rejections,
		}, nil
	}
	selected := mostFrequent(candidates)
	selected.Source = SourceLLM
	if p.samples > 1 {
		selected.Source = SourceConsensus
	}
	selected.Attempts = attempts
	selected.AcceptedCandidates = len(candidates)
	selected.Rejections = rejections
	return selected, nil
}

func (p *LLMPlanner) systemPrompt(intents []manipulation.Intent) string {
	catalogBytes, _ := json.MarshalIndent(skillCatalogView(p.catalog), "", "  ")
	intentBytes, _ := json.MarshalIndent(intents, "", "  ")
	return fmt.Sprintf(`You are a robot task tasks. Produce a deterministic, executable plan for the ordered intents below.

Available skills (only these names are valid):
%s

Intents (one plan per intent, in the same order):
%s

Rules:
- Return only one JSON object with a "plans" array.
- Each plan contains "id", "goal", and "steps".
- Each step contains "id", "skill", "arguments", and optional "dependsOn".
- Use exactly "@object" and "@destination" as string placeholders for the grounded entity ids. Do not invent entity ids.
- Do not include safety fields (approvalId, deadlineUnixMs, leaseMs, idempotencyKey, safetyLevel); the Robot Runtime fills them.
- Use read_only skills before physical_motion skills and verify physical outcomes afterwards.
- A plan must contain at least one side-effect skill; otherwise it cannot complete a manipulation goal.

Example:
{
  "plans": [
    {
      "id": "task-example",
      "goal": "pick and place an object",
      "steps": [
        {"id": "observe", "skill": "observe_scene", "arguments": {}},
        {"id": "resolve", "skill": "resolve_targets", "arguments": {"objectId": "@object", "destinationId": "@destination"}, "dependsOn": ["observe"]},
        {"id": "plan_grasp", "skill": "plan_grasp", "arguments": {"objectId": "@object", "destinationId": "@destination"}, "dependsOn": ["resolve"]},
        {"id": "pick", "skill": "manipulation.pick", "arguments": {"targetRef": "@object"}, "dependsOn": ["plan_grasp"]},
        {"id": "verify_grasp", "skill": "verify_grasp", "arguments": {"objectId": "@object"}, "dependsOn": ["pick"]},
        {"id": "place", "skill": "manipulation.place", "arguments": {"targetRef": "@destination"}, "dependsOn": ["verify_grasp"]},
        {"id": "verify_place", "skill": "verify_placement", "arguments": {"objectId": "@object", "destinationId": "@destination"}, "dependsOn": ["place"]}
      ]
    }
  ]
}`, string(catalogBytes), string(intentBytes))
}

type skillView struct {
	Name               string   `json:"name"`
	Description        string   `json:"description"`
	SafetyLevel        string   `json:"safetyLevel"`
	SideEffect         bool     `json:"sideEffect"`
	RequiredParameters []string `json:"requiredParameters,omitempty"`
}

func skillCatalogView(catalog []skills.SkillManifest) []skillView {
	view := make([]skillView, 0, len(catalog))
	for _, manifest := range catalog {
		view = append(view, skillView{
			Name:               manifest.Name,
			Description:        manifest.Description,
			SafetyLevel:        string(manifest.SafetyLevel),
			SideEffect:         manifest.SideEffect,
			RequiredParameters: manifest.RequiredParameters,
		})
	}
	return view
}

type chatCompletionRequest struct {
	Model    string        `json:"model"`
	Messages []chatMessage `json:"messages"`
}

type chatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type chatCompletionResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
}

func (p *LLMPlanner) post(body []byte) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, p.baseURL+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+p.apiKey)
	response, err := p.client.Do(request)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		message, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return "", fmt.Errorf("llm status %d: %s", response.StatusCode, strings.TrimSpace(string(message)))
	}
	var completion chatCompletionResponse
	if err := json.NewDecoder(response.Body).Decode(&completion); err != nil {
		return "", err
	}
	if len(completion.Choices) == 0 {
		return "", errors.New("llm returned no choices")
	}
	content := completion.Choices[0].Message.Content
	if strings.TrimSpace(content) == "" {
		return "", errors.New("llm returned empty plan")
	}
	return content, nil
}

func parseBundle(content string) (Bundle, error) {
	content = strings.TrimSpace(content)
	start := strings.Index(content, "{")
	end := strings.LastIndex(content, "}")
	if start < 0 || end <= start {
		return Bundle{}, errors.New("llm plan is not a JSON object")
	}
	var bundle Bundle
	if err := json.Unmarshal([]byte(content[start:end+1]), &bundle); err != nil {
		return Bundle{}, err
	}
	if len(bundle.Plans) == 0 {
		return Bundle{}, errors.New("llm plan contains no plans")
	}
	return bundle, nil
}

func validateBundle(bundle Bundle, intents []manipulation.Intent, catalog []skills.SkillManifest) error {
	if len(bundle.Plans) != len(intents) {
		return fmt.Errorf("plan count %d does not match intent count %d", len(bundle.Plans), len(intents))
	}
	byName := make(map[string]skills.SkillManifest, len(catalog))
	for _, manifest := range catalog {
		byName[manifest.Name] = manifest
	}
	for index, plan := range bundle.Plans {
		if len(plan.Steps) == 0 {
			return fmt.Errorf("plan %d has no steps", index)
		}
		if err := plan.ValidateShape(); err != nil {
			return fmt.Errorf("plan %d shape: %w", index, err)
		}
		hasSideEffect := false
		for _, step := range plan.Steps {
			manifest, ok := byName[step.Skill]
			if !ok {
				return fmt.Errorf("plan %d step %s uses unknown skill %s", index, step.ID, step.Skill)
			}
			for _, required := range manifest.RequiredParameters {
				if _, ok := step.Arguments[required]; !ok {
					return fmt.Errorf("plan %d step %s missing required argument %s", index, step.ID, required)
				}
			}
			if manifest.SideEffect {
				hasSideEffect = true
			}
		}
		if !hasSideEffect {
			return fmt.Errorf("plan %d contains no side-effect skill", index)
		}
	}
	return nil
}

func hashBundle(bundle Bundle) string {
	bytesValue, _ := json.Marshal(bundle.Plans)
	sum := sha256.Sum256(bytesValue)
	return hex.EncodeToString(sum[:])
}

func mostFrequent(candidates []candidate) Bundle {
	counts := make(map[string]int, len(candidates))
	first := make(map[string]Bundle, len(candidates))
	for _, candidate := range candidates {
		counts[candidate.hash]++
		if _, ok := first[candidate.hash]; !ok {
			first[candidate.hash] = candidate.bundle
		}
	}
	hashes := make([]string, 0, len(counts))
	for hash := range counts {
		hashes = append(hashes, hash)
	}
	sort.Slice(hashes, func(i, j int) bool {
		if counts[hashes[i]] == counts[hashes[j]] {
			return hashes[i] < hashes[j]
		}
		return counts[hashes[i]] > counts[hashes[j]]
	})
	return first[hashes[0]]
}
