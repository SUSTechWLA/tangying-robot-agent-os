// Package cloudclient is the edge worker's HTTP data-plane client for the
// Fleet control plane. It pulls ready task ids, fetches tasks, claims and
// completes intents, and reports telemetry/events. Authentication uses the
// robot-specific device credential headers; the mTLS gRPC Link channel (link.go) is the
// control plane for heartbeat/lease and server commands.
package cloudclient

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	fleettelemetry "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Config configures the HTTP data-plane client.
type Config struct {
	// BaseURL is the fleet control-plane base URL, e.g.
	// https://fleet.example.com (through the reverse proxy) or
	// http://127.0.0.1:8080 (internal).
	BaseURL string
	// RobotID and DeviceToken form one provisioned device principal. Tokens
	// must be distinct per robot.
	RobotID     string
	DeviceToken string
	// CAFile optionally pins the fleet CA so the worker can verify the
	// reverse proxy's TLS certificate (self-signed fleet certs).
	CAFile string
	// ServerName overrides the TLS server name for CAFile verification.
	ServerName string
	// LongPollTimeout bounds each /v1/queue/next request.
	LongPollTimeout time.Duration
}

type Client struct {
	baseURL     string
	robotID     string
	deviceToken string
	httpClient  *http.Client
}

func New(config Config) *Client {
	if config.LongPollTimeout <= 0 {
		config.LongPollTimeout = 35 * time.Second
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if config.CAFile != "" {
		caBytes, err := os.ReadFile(config.CAFile)
		if err == nil {
			roots := x509.NewCertPool()
			if roots.AppendCertsFromPEM(caBytes) {
				transport.TLSClientConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
				if config.ServerName != "" {
					transport.TLSClientConfig.ServerName = config.ServerName
				}
			}
		}
	}
	return &Client{
		baseURL:     config.BaseURL,
		robotID:     config.RobotID,
		deviceToken: config.DeviceToken,
		httpClient:  &http.Client{Timeout: config.LongPollTimeout + 5*time.Second, Transport: transport},
	}
}

// ErrTaskUnavailable is returned when the long-poll returns no task.
var ErrTaskUnavailable = errors.New("no ready task")

var ErrWorldNotReady = errors.New("world evidence is not ready")

type APIError struct {
	StatusCode int
	Code       string
	Message    string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("fleet API %d %s: %s", e.StatusCode, e.Code, e.Message)
}

func (e *APIError) Is(target error) bool {
	return target == ErrWorldNotReady && e.Code == "WORLD_NOT_READY"
}

// NextTask long-polls the ready queue for this robot (own queue, then the
// shared any queue on the server side) and returns a task id, or
// ErrTaskUnavailable when the poll window expires.
func (c *Client) NextTask(ctx context.Context, robotID string) (string, error) {
	url := fmt.Sprintf("%s/v1/queue/next?robot_id=%s", c.baseURL, robotID)
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	c.authenticate(request)
	response, err := c.httpClient.Do(request)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", c.statusError(response)
	}
	var body struct {
		TaskID string `json:"taskId"`
	}
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
		return "", err
	}
	if body.TaskID == "" {
		return "", ErrTaskUnavailable
	}
	return body.TaskID, nil
}

// GetTask fetches a full task (plan, intents, state).
func (c *Client) GetTask(ctx context.Context, taskID string) (*tasks.Task, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, fmt.Sprintf("%s/v1/tasks/%s", c.baseURL, taskID), nil)
	if err != nil {
		return nil, err
	}
	c.authenticate(request)
	response, err := c.httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, c.statusError(response)
	}
	var task tasks.Task
	if err := json.NewDecoder(response.Body).Decode(&task); err != nil {
		return nil, err
	}
	return &task, nil
}

// NextIntent claims the next intent this worker may run, or returns nil
// when nothing is claimable right now.
func (c *Client) NextIntent(ctx context.Context, taskID, robotID string) (*coordinator.IntentNode, error) {
	url := fmt.Sprintf("%s/v1/tasks/%s/intents/next", c.baseURL, taskID)
	body, err := json.Marshal(map[string]string{"robotId": robotID})
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	c.authenticate(request)
	response, err := c.httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, c.statusError(response)
	}
	var payload struct {
		Intent *coordinator.IntentNode `json:"intent"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return nil, err
	}
	return payload.Intent, nil
}

// CompleteIntent reports intent success to the coordinator.
func (c *Client) CompleteIntent(ctx context.Context, taskID string, index int, robotID string) error {
	return c.intentAction(ctx, taskID, index, "complete", map[string]string{"robotId": robotID})
}

// CompleteIntentRevision reports the exact immutable claim coordinates. The
// coordinator rejects delayed or cross-revision acknowledgements.
func (c *Client) CompleteIntentRevision(ctx context.Context, taskID string, node *coordinator.IntentNode, robotID string) error {
	if node == nil {
		return errors.New("intent identity is required")
	}
	return c.intentAction(ctx, taskID, node.Index, "complete", map[string]any{
		"robotId": robotID, "taskRevision": node.TaskRevision, "aggregateVersion": node.AggregateVersion,
		"stepId": node.StepID, "commandId": node.CommandID, "fencingToken": node.FencingToken,
	})
}

// FailIntent reports intent failure to the coordinator.
func (c *Client) FailIntent(ctx context.Context, taskID string, index int, robotID, reason string) error {
	return c.intentAction(ctx, taskID, index, "fail", map[string]string{"robotId": robotID, "reason": reason})
}

func (c *Client) FailIntentRevision(ctx context.Context, taskID string, node *coordinator.IntentNode, robotID, reason string) error {
	if node == nil {
		return errors.New("intent identity is required")
	}
	return c.intentAction(ctx, taskID, node.Index, "fail", map[string]any{
		"robotId": robotID, "reason": reason, "taskRevision": node.TaskRevision,
		"aggregateVersion": node.AggregateVersion, "stepId": node.StepID,
		"commandId": node.CommandID, "fencingToken": node.FencingToken,
	})
}

func (c *Client) intentAction(ctx context.Context, taskID string, index int, action string, payload any) error {
	url := fmt.Sprintf("%s/v1/tasks/%s/intents/%d/%s", c.baseURL, taskID, index, action)
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	c.authenticate(request)
	response, err := c.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return c.statusError(response)
	}
	return nil
}

// ReportTelemetry POSTs one telemetry sample to the fleet sink.
func (c *Client) ReportTelemetry(ctx context.Context, sample fleettelemetry.Sample) error {
	body, err := json.Marshal(sample)
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/v1/telemetry", bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	c.authenticate(request)
	response, err := c.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusAccepted {
		return c.statusError(response)
	}
	return nil
}

// AppendEvent appends a task event (step-level progress) to the task log.
func (c *Client) AppendEvent(ctx context.Context, taskID, eventType, stepID, message string, payload map[string]any) error {
	body, err := json.Marshal(map[string]any{
		"type": eventType, "stepId": stepID, "message": message, "payload": payload,
	})
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, fmt.Sprintf("%s/v1/tasks/%s/events", c.baseURL, taskID), bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	c.authenticate(request)
	response, err := c.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		return c.statusError(response)
	}
	return nil
}

func (c *Client) statusError(response *http.Response) error {
	raw, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
	message := strings.TrimSpace(string(raw))
	var payload struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	}
	if json.Unmarshal(raw, &payload) == nil && payload.Code != "" {
		return &APIError{StatusCode: response.StatusCode, Code: payload.Code, Message: payload.Message}
	}
	if message == "" {
		message = response.Status
	}
	return fmt.Errorf("fleet API %s: %s", response.Status, message)
}

func (c *Client) authenticate(request *http.Request) {
	request.Header.Set("X-Robot-ID", c.robotID)
	request.Header.Set("X-Device-Token", c.deviceToken)
}
