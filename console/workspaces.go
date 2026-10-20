package console

// Workspaces select a separately running, single-robot Local Agent by URL
// scope. They never switch this process's Runtime, store, or control lease.
// The registry is operator-provisioned and immutable for a process lifetime.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	operatorweb "github.com/SUSTechWLA/tangying-robot-agent-os/web"
	"github.com/gorilla/websocket"
	"golang.org/x/sys/unix"
)

const WorkspaceIdentityHeader = "X-Tangying-Robot-ID"

type workspaceProfile struct {
	ID          string `json:"id"`
	Label       string `json:"label"`
	RobotID     string `json:"robotId"`
	URL         string `json:"url"`
	SessionFile string `json:"sessionFile"`
	endpoint    *url.URL
}

// WorkspaceRegistry has no mutation API. Credentials and upstream addresses
// are deliberately absent from its browser representation.
type WorkspaceRegistry struct {
	profiles  []workspaceProfile
	transport *http.Transport
}

// LoadWorkspacesFile validates configuration without contacting a robot.
// A missing credential file is allowed at startup: the UI must still display
// an offline robot. Reads and writes fail closed until it is available.
func LoadWorkspacesFile(filename string) (*WorkspaceRegistry, error) {
	if filename == "" {
		return nil, nil
	}
	file, err := os.Open(filename)
	if err != nil {
		return nil, fmt.Errorf("open workspaces configuration: %w", err)
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, 65537))
	if err != nil || len(data) > 65536 {
		return nil, errors.New("workspaces configuration must be at most 64 KiB")
	}
	var document struct {
		SchemaVersion string             `json:"schemaVersion"`
		Workspaces    []workspaceProfile `json:"workspaces"`
	}
	if err := rejectDuplicateWorkspaceKeys(data); err != nil {
		return nil, fmt.Errorf("invalid workspaces JSON: %w", err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&document); err != nil {
		return nil, fmt.Errorf("decode workspaces configuration: %w", err)
	}
	if document.SchemaVersion != "edge.workspaces.v1" || len(document.Workspaces) > 32 {
		return nil, errors.New("workspaces require edge.workspaces.v1 and at most 32 remote profiles")
	}
	ids, robots, endpoints := map[string]bool{"local": true}, map[string]bool{}, map[string]bool{}
	idPattern := regexp.MustCompile(`^[a-z][a-z0-9-]{0,47}$`)
	for i := range document.Workspaces {
		profile := &document.Workspaces[i]
		if !idPattern.MatchString(profile.ID) || ids[profile.ID] {
			return nil, errors.New("workspace IDs must be unique lowercase slugs; local is reserved")
		}
		if strings.TrimSpace(profile.Label) == "" || len(profile.Label) > 160 ||
			strings.TrimSpace(profile.RobotID) == "" || strings.TrimSpace(profile.RobotID) != profile.RobotID ||
			len(profile.RobotID) > 256 || strings.ContainsAny(profile.RobotID, "\r\n\t") || robots[profile.RobotID] {
			return nil, errors.New("workspaces require labels and unique, nonempty robot IDs")
		}
		endpoint, err := url.Parse(profile.URL)
		if err != nil || endpoint == nil || endpoint.Hostname() == "" || endpoint.Opaque != "" || endpoint.User != nil ||
			(endpoint.Path != "" && endpoint.Path != "/") || endpoint.RawPath != "" ||
			endpoint.RawQuery != "" || endpoint.ForceQuery || endpoint.Fragment != "" || endpoint.RawFragment != "" || strings.ContainsAny(profile.URL, "?#") {
			return nil, errors.New("workspace URL must be an HTTP(S) origin without credentials, query, fragment, or path")
		}
		if port := endpoint.Port(); port != "" {
			number, err := strconv.Atoi(port)
			if err != nil || number < 1 || number > 65535 {
				return nil, errors.New("workspace URL has an invalid port")
			}
		}
		if endpoint.Scheme != "https" {
			ip := net.ParseIP(endpoint.Hostname())
			if endpoint.Scheme != "http" || ip == nil || !ip.IsLoopback() {
				return nil, errors.New("workspace HTTP is allowed only for literal loopback IP addresses; use verified HTTPS otherwise")
			}
		}
		endpoint.Path = ""
		if endpoints[endpoint.String()] {
			return nil, errors.New("workspace endpoints must be unique")
		}
		if !filepath.IsAbs(profile.SessionFile) || filepath.Clean(profile.SessionFile) != profile.SessionFile {
			return nil, errors.New("workspace sessionFile must be an absolute, clean server-side path")
		}
		ids[profile.ID], robots[profile.RobotID], endpoints[endpoint.String()] = true, true, true
		profile.endpoint = endpoint
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil // Operator allowlist must not leak tokens through HTTP_PROXY.
	// Task creation can synchronously perform several bounded model rounds.
	// Preserve the supported 600s planning window; identity probes have their
	// own much shorter context deadline and do not consume this allowance.
	transport.ResponseHeaderTimeout = 10 * time.Minute
	return &WorkspaceRegistry{profiles: document.Workspaces, transport: transport}, nil
}

// ValidateIdentity prevents a remote alias from claiming this process's robot.
func (registry *WorkspaceRegistry) ValidateIdentity(robotID string) error {
	if registry == nil {
		return nil
	}
	for _, profile := range registry.profiles {
		if profile.RobotID == robotID {
			return errors.New("remote workspace robot ID duplicates this Local Agent's Runtime identity")
		}
	}
	return nil
}

func WithWorkspaceIdentity(robotID string) Option {
	return func(server *Server) { server.workspaceRobotID = robotID }
}

func WithWorkspaces(registry *WorkspaceRegistry) Option {
	return func(server *Server) { server.workspaces = registry }
}

func (s *Server) workspaceIdentity(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if s.workspaceRobotID == "" {
		writeError(w, http.StatusServiceUnavailable, "WORKSPACE_IDENTITY_UNAVAILABLE", "Local Agent has no bound robot identity")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"schemaVersion": "edge.workspace.v1", "robotId": s.workspaceRobotID})
}

func (s *Server) listWorkspaces(w http.ResponseWriter, _ *http.Request) {
	s.writeWorkspaces(w, "local")
}

func (s *Server) writeWorkspaces(w http.ResponseWriter, current string) {
	type entry struct {
		ID      string `json:"id"`
		Label   string `json:"label"`
		RobotID string `json:"robotId"`
		Href    string `json:"href"`
	}
	entries := []entry{{"local", "本机机器人", s.workspaceRobotID, "/"}}
	if s.workspaces != nil {
		for _, profile := range s.workspaces.profiles {
			entries = append(entries, entry{profile.ID, profile.Label, profile.RobotID, "/robots/" + profile.ID + "/"})
		}
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusOK, map[string]any{"schemaVersion": "edge.workspaces.v1", "currentId": current, "workspaces": entries})
}

// withWorkspaces runs before ServeMux's path cleanup/redirects. A malformed or
// unknown robot scope never falls back to this process's robot.
func (s *Server) withWorkspaces(next http.Handler) http.Handler {
	static := operatorweb.Handler()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/robots/") {
			if r.URL.RawPath != "" || path.Clean(r.URL.Path) != strings.TrimSuffix(r.URL.Path, "/") {
				writeError(w, http.StatusBadRequest, "WORKSPACE_PATH_INVALID", "invalid workspace path")
				return
			}
			id, suffix, _ := strings.Cut(strings.TrimPrefix(r.URL.Path, "/robots/"), "/")
			var profile *workspaceProfile
			if s.workspaces != nil {
				for i := range s.workspaces.profiles {
					if s.workspaces.profiles[i].ID == id {
						profile = &s.workspaces.profiles[i]
						break
					}
				}
			}
			if profile == nil {
				writeError(w, http.StatusNotFound, "WORKSPACE_NOT_FOUND", "unknown robot workspace")
				return
			}
			if suffix == "" && !strings.HasSuffix(r.URL.Path, "/") {
				if r.Method != http.MethodGet {
					w.WriteHeader(http.StatusMethodNotAllowed)
					return
				}
				http.Redirect(w, r, "/robots/"+id+"/", http.StatusTemporaryRedirect)
				return
			}
			route := "/" + suffix
			if route == "/v1/workspaces" {
				if r.Method != http.MethodGet {
					w.WriteHeader(http.StatusMethodNotAllowed)
					return
				}
				s.writeWorkspaces(w, id)
				return
			}
			if strings.HasPrefix(route, "/v1/") || route == "/healthz" {
				s.proxyWorkspace(w, r, *profile, route)
				return
			}
			// HTML, JavaScript and bundled assets are always served by this
			// console, never executable upstream content on the root origin.
			if r.Method != http.MethodGet && r.Method != http.MethodHead {
				w.WriteHeader(http.StatusMethodNotAllowed)
				return
			}
			copy := r.Clone(r.Context())
			copy.URL.Path, copy.URL.RawPath = route, ""
			static.ServeHTTP(w, copy)
			return
		}
		if expected := r.Header.Get(WorkspaceIdentityHeader); expected != "" && expected != s.workspaceRobotID {
			writeError(w, http.StatusConflict, "WORKSPACE_IDENTITY_MISMATCH", "request robot identity does not match this Local Agent")
			return
		}
		if s.workspaceRobotID != "" {
			w.Header().Set(WorkspaceIdentityHeader, s.workspaceRobotID)
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) proxyWorkspace(w http.ResponseWriter, r *http.Request, profile workspaceProfile, route string) {
	if !s.allowOperatorWrite(w, r) { // Reads/WS also must not elevate anonymous clients.
		return
	}
	if route == "/v1/robots/pair" || strings.HasPrefix(route, "/v1/workspaces/") {
		writeError(w, http.StatusForbidden, "WORKSPACE_OPERATION_FORBIDDEN", "pairing and registry changes require direct local administration")
		return
	}
	if expected := r.Header.Get(WorkspaceIdentityHeader); expected != "" && expected != profile.RobotID {
		writeError(w, http.StatusConflict, "WORKSPACE_IDENTITY_MISMATCH", "request robot identity does not match selected workspace")
		return
	}
	// Probe without any session credential. A reused endpoint must not get the
	// saved bearer token before its pinned immutable identity is checked.
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	probe, _ := http.NewRequestWithContext(ctx, http.MethodGet, profile.endpoint.String()+"/v1/workspace", nil)
	probe.Header.Set(WorkspaceIdentityHeader, profile.RobotID)
	client := &http.Client{Transport: s.workspaces.transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(probe)
	if err != nil {
		cancel()
		writeError(w, http.StatusBadGateway, "WORKSPACE_UNAVAILABLE", "selected robot Local Agent is unreachable")
		return
	}
	var identity struct {
		SchemaVersion string `json:"schemaVersion"`
		RobotID       string `json:"robotId"`
	}
	data, readErr := io.ReadAll(io.LimitReader(response.Body, 4097))
	response.Body.Close()
	cancel()
	if response.StatusCode != http.StatusOK || readErr != nil || len(data) > 4096 ||
		json.Unmarshal(data, &identity) != nil || identity.SchemaVersion != "edge.workspace.v1" || identity.RobotID != profile.RobotID {
		writeError(w, http.StatusBadGateway, "WORKSPACE_IDENTITY_MISMATCH", "selected endpoint did not prove the expected robot identity")
		return
	}
	token, err := workspaceSession(profile.SessionFile)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "WORKSPACE_SESSION_UNAVAILABLE", "selected robot session credential is unavailable or not private")
		return
	}
	proxy := &httputil.ReverseProxy{
		Transport: s.workspaces.transport,
		Rewrite: func(request *httputil.ProxyRequest) {
			request.SetURL(profile.endpoint)
			request.Out.URL.Path, request.Out.URL.RawPath = route, ""
			request.Out.Host = profile.endpoint.Host
			headers := make(http.Header)
			for _, name := range []string{"Accept", "Accept-Encoding", "Content-Type", "Range", "If-None-Match", "If-Modified-Since", "Sec-WebSocket-Key", "Sec-WebSocket-Version", "Sec-WebSocket-Protocol", "Sec-WebSocket-Extensions", "Connection", "Upgrade"} {
				if values := request.Out.Header.Values(name); len(values) != 0 {
					headers[name] = append([]string(nil), values...)
				}
			}
			headers.Set("Origin", profile.endpoint.String())
			headers.Set(SessionHeaderName, token)
			headers.Set(WorkspaceIdentityHeader, profile.RobotID)
			request.Out.Header = headers
		},
		ModifyResponse: func(response *http.Response) error {
			response.Header.Del("Set-Cookie")
			response.Header.Del("Access-Control-Allow-Origin")
			response.Header.Del("Access-Control-Allow-Credentials")
			response.Header.Set("Cache-Control", "no-store")
			// API bodies do not get to install executable documents on the
			// console origin, even if an upstream is misconfigured.
			response.Header.Set("Content-Security-Policy", "default-src 'none'; sandbox; frame-ancestors 'none'")
			response.Header.Set("X-Content-Type-Options", "nosniff")
			media, _, _ := mime.ParseMediaType(response.Header.Get("Content-Type"))
			switch media {
			case "text/html", "application/xhtml+xml", "application/javascript", "text/javascript", "image/svg+xml":
				return errors.New("workspace API returned executable content")
			}
			if response.StatusCode >= 300 && response.StatusCode < 400 && response.StatusCode != http.StatusNotModified {
				return errors.New("workspace redirects are not permitted")
			}
			return nil
		},
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) {
			writeError(w, http.StatusBadGateway, "WORKSPACE_UPSTREAM_FAILED", "selected robot connection failed")
		},
	}
	proxy.ServeHTTP(w, r)
}

func workspaceSession(filename string) (string, error) {
	// Reject directories, links and named pipes before opening; a configured
	// FIFO must not strand an operator request waiting for a writer.
	linkInfo, err := os.Lstat(filename)
	if err != nil || !linkInfo.Mode().IsRegular() {
		return "", errors.New("workspace session must be a regular file, not a link or pipe")
	}
	fd, err := unix.Open(filename, unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		return "", err
	}
	file := os.NewFile(uintptr(fd), filename)
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 4096 {
		return "", errors.New("workspace session must be a private regular file")
	}
	data, err := io.ReadAll(io.LimitReader(file, 4097))
	token := strings.TrimSpace(string(data))
	if err != nil || len(data) > 4096 || token == "" || strings.ContainsAny(token, " \t\r\n") {
		return "", errors.New("invalid workspace session")
	}
	return token, nil
}

func rejectDuplicateWorkspaceKeys(data []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	var consume func(int) error
	consume = func(depth int) error {
		if depth > 8 {
			return errors.New("JSON nesting limit exceeded")
		}
		token, err := decoder.Token()
		if err != nil {
			return err
		}
		switch token {
		case json.Delim('{'):
			keys := map[string]bool{}
			for decoder.More() {
				key, err := decoder.Token()
				if err != nil {
					return err
				}
				name, ok := key.(string)
				if !ok || keys[name] {
					return errors.New("duplicate or invalid object key")
				}
				keys[name] = true
				if err := consume(depth + 1); err != nil {
					return err
				}
			}
			_, err = decoder.Token()
			return err
		case json.Delim('['):
			for decoder.More() {
				if err := consume(depth + 1); err != nil {
					return err
				}
			}
			_, err = decoder.Token()
			return err
		}
		return nil
	}
	if err := consume(0); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return errors.New("trailing JSON data")
	}
	return nil
}

// workspaceSocketContext detects a browser leaving even when the event stream
// is idle. HTTP request cancellation alone does not observe a hijacked socket's
// close. Gorilla permits one reader alongside the handler's single writer.
func workspaceSocketContext(parent context.Context, connection *websocket.Conn) (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancel(parent)
	context.AfterFunc(ctx, func() { _ = connection.Close() })
	connection.SetReadLimit(1024)
	go func() {
		defer cancel()
		for {
			if _, _, err := connection.ReadMessage(); err != nil {
				return
			}
		}
	}()
	return ctx, cancel
}
