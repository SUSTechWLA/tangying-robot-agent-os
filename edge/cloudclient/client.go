package cloudclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
)

type Client struct {
	baseURL string
	http    *http.Client
}

func New(baseURL string) *Client {
	return &Client{baseURL: strings.TrimRight(baseURL, "/"), http: &http.Client{Timeout: 30 * time.Second}}
}

func (c *Client) Claim(ctx context.Context, agentID string) (orchestrator.Claim, error) {
	var claim orchestrator.Claim
	err := c.post(ctx, "/v1/agents/"+agentID+"/claim", map[string]any{}, &claim)
	return claim, err
}

func (c *Client) SetState(ctx context.Context, taskID, state, reason string) error {
	return c.post(ctx, "/v1/tasks/"+taskID+"/state", map[string]string{"state": state, "reason": reason}, nil)
}

func (c *Client) AppendEvent(ctx context.Context, taskID string, event orchestrator.TaskEvent) error {
	return c.post(ctx, "/v1/tasks/"+taskID+"/events", event, nil)
}

func (c *Client) post(ctx context.Context, path string, input, output any) error {
	body, err := json.Marshal(input)
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := c.http.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		var failure map[string]any
		_ = json.NewDecoder(response.Body).Decode(&failure)
		return fmt.Errorf("cloud request %s failed with status %d: %v", path, response.StatusCode, failure)
	}
	if output != nil {
		return json.NewDecoder(response.Body).Decode(output)
	}
	return nil
}
