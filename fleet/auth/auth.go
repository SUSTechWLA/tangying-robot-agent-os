// Package auth implements the Fleet control-plane authentication boundary.
//
// Two principals exist:
//
//   - Operators (humans): log in with credentials supplied through the
//     FLEET_OPERATOR_USER / FLEET_OPERATOR_PASSWORD environment variables and
//     receive a short-lived HMAC-signed bearer token for console routes.
//   - Devices (edge workers / robots): authenticate with a robot-specific
//     credential from FLEET_DEVICE_CREDENTIALS and may only reach the device
//     data-plane routes.
//
// The robot-facing gRPC gateway never goes through this package: it is
// authenticated by the mTLS client certificate presented on the Link stream.
package auth

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	pathpkg "path"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	// TokenLifetime is how long an operator bearer token stays valid.
	TokenLifetime     = 24 * time.Hour
	WSTicketLifetime  = 60 * time.Second
	headerName        = "Authorization"
	deviceHeader      = "X-Device-Token"
	deviceRobotHeader = "X-Robot-ID"
)

var (
	ErrInvalidCredentials = errors.New("invalid operator credentials")
	ErrInvalidToken       = errors.New("invalid or expired token")
	ErrDeviceToken        = errors.New("invalid device token")
	ErrInvalidWSTicket    = errors.New("invalid, expired, or consumed websocket ticket")
)

// Authenticator issues and verifies operator tokens and checks the device
// token. The HMAC secret comes from FLEET_AUTH_SECRET or a fresh random
// secret when unset (sessions then do not survive a restart).
type Authenticator struct {
	secret       []byte
	operatorUser string
	operatorPass string
	deviceTokens map[string]string
	now          func() time.Time
	wsMu         sync.Mutex
	wsTickets    map[string]wsTicket
}

type wsTicket struct {
	Subject string
	Scope   string
	Expiry  time.Time
}

type Options struct {
	OperatorUser string
	OperatorPass string
	DeviceToken  string
	// DeviceCredentials maps each provisioned robot id to a distinct secret.
	// DeviceToken is retained in Options for source compatibility but is not
	// accepted on the multi-robot data plane because it cannot bind identity.
	DeviceCredentials map[string]string
	Secret            string
	Now               func() time.Time
}

func New(options Options) (*Authenticator, error) {
	secret := options.Secret
	if secret == "" {
		secret = os.Getenv("FLEET_AUTH_SECRET")
	}
	if secret == "" {
		random := make([]byte, 32)
		if _, err := rand.Read(random); err != nil {
			return nil, err
		}
		secret = hex.EncodeToString(random)
	}
	user := options.OperatorUser
	if user == "" {
		user = os.Getenv("FLEET_OPERATOR_USER")
	}
	pass := options.OperatorPass
	if pass == "" {
		pass = os.Getenv("FLEET_OPERATOR_PASSWORD")
	}
	devices := cloneCredentials(options.DeviceCredentials)
	if len(devices) == 0 {
		var err error
		devices, err = parseDeviceCredentials(os.Getenv("FLEET_DEVICE_CREDENTIALS"))
		if err != nil {
			return nil, err
		}
	}
	now := options.Now
	if now == nil {
		now = time.Now
	}
	return &Authenticator{
		secret: []byte(secret), operatorUser: user, operatorPass: pass,
		deviceTokens: devices, now: now, wsTickets: map[string]wsTicket{},
	}, nil
}

func (a *Authenticator) IssueWSTicket(_ context.Context, subject, scope string) (string, time.Time, error) {
	if strings.TrimSpace(subject) == "" || strings.TrimSpace(scope) == "" {
		return "", time.Time{}, ErrInvalidWSTicket
	}
	random := make([]byte, 32)
	if _, err := rand.Read(random); err != nil {
		return "", time.Time{}, err
	}
	raw := base64.RawURLEncoding.EncodeToString(random)
	digest := sha256.Sum256([]byte(raw))
	key := hex.EncodeToString(digest[:])
	expiry := a.now().UTC().Add(WSTicketLifetime)
	a.wsMu.Lock()
	for stored, ticket := range a.wsTickets {
		if !a.now().UTC().Before(ticket.Expiry) {
			delete(a.wsTickets, stored)
		}
	}
	a.wsTickets[key] = wsTicket{Subject: subject, Scope: scope, Expiry: expiry}
	a.wsMu.Unlock()
	return raw, expiry, nil
}

func (a *Authenticator) ConsumeWSTicket(_ context.Context, raw, scope string) (string, error) {
	digest := sha256.Sum256([]byte(raw))
	key := hex.EncodeToString(digest[:])
	a.wsMu.Lock()
	ticket, ok := a.wsTickets[key]
	delete(a.wsTickets, key)
	a.wsMu.Unlock()
	if !ok || ticket.Scope != scope || !a.now().UTC().Before(ticket.Expiry) {
		return "", ErrInvalidWSTicket
	}
	return ticket.Subject, nil
}

// Defaults are demo credentials only when the operator explicitly enables
// them through FLEET_OPERATOR_USER; otherwise login is disabled.
func (a *Authenticator) OperatorConfigured() bool { return a.operatorUser != "" }

func (a *Authenticator) Login(ctx context.Context, user, password string) (string, time.Time, error) {
	if a.operatorUser == "" {
		return "", time.Time{}, ErrInvalidCredentials
	}
	if subtle.ConstantTimeCompare([]byte(user), []byte(a.operatorUser)) != 1 ||
		subtle.ConstantTimeCompare([]byte(password), []byte(a.operatorPass)) != 1 {
		return "", time.Time{}, ErrInvalidCredentials
	}
	issued := a.now().UTC()
	expiry := issued.Add(TokenLifetime)
	claims := map[string]any{
		"sub": user,
		"iat": issued.Unix(),
		"exp": expiry.Unix(),
	}
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"HS256","typ":"fleet-token"}`))
	payload, err := json.Marshal(claims)
	if err != nil {
		return "", time.Time{}, err
	}
	signingInput := header + "." + base64.RawURLEncoding.EncodeToString(payload)
	signature := a.sign([]byte(signingInput))
	return signingInput + "." + signature, expiry, nil
}

func (a *Authenticator) sign(input []byte) string {
	mac := hmac.New(sha256.New, a.secret)
	_, _ = mac.Write(input)
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func (a *Authenticator) VerifyToken(ctx context.Context, token string) (string, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return "", ErrInvalidToken
	}
	expected := a.sign([]byte(parts[0] + "." + parts[1]))
	if subtle.ConstantTimeCompare([]byte(parts[2]), []byte(expected)) != 1 {
		return "", ErrInvalidToken
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return "", ErrInvalidToken
	}
	var claims struct {
		Sub string `json:"sub"`
		Exp int64  `json:"exp"`
	}
	if err := json.Unmarshal(payload, &claims); err != nil {
		return "", ErrInvalidToken
	}
	if claims.Sub == "" || a.now().Unix() >= claims.Exp {
		return "", ErrInvalidToken
	}
	return claims.Sub, nil
}

// DeviceTokenValid reports whether token is the credential provisioned for
// exactly robotID. A token for one robot can never authenticate another.
func (a *Authenticator) DeviceTokenValid(ctx context.Context, robotID, token string) bool {
	expected := a.deviceTokens[robotID]
	if expected == "" || token == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(token), []byte(expected)) == 1
}

type deviceRoute struct {
	method     string
	pattern    string
	deviceOnly bool
}

var deviceRoutes = []deviceRoute{
	{http.MethodGet, "/v1/queue/next", true},
	{http.MethodGet, "/v1/tasks/{id}", false},
	{http.MethodPost, "/v1/telemetry", true},
	{http.MethodPost, "/v1/tasks/{id}/events", true},
	{http.MethodPost, "/v1/tasks/{id}/intents/next", true},
	{http.MethodPost, "/v1/tasks/{id}/intents/{index}/complete", true},
	{http.MethodPost, "/v1/tasks/{id}/intents/{index}/fail", true},
}

type principalKey struct{}

type Principal struct {
	Role    string
	Subject string
	RobotID string
}

func PrincipalFromContext(ctx context.Context) (Principal, bool) {
	principal, ok := ctx.Value(principalKey{}).(Principal)
	return principal, ok
}

func DeviceRobotID(ctx context.Context) (string, bool) {
	principal, ok := PrincipalFromContext(ctx)
	return principal.RobotID, ok && principal.Role == "device" && principal.RobotID != ""
}

// RequireAuth wraps the fleet mux. Requests carrying a valid operator bearer
// token are allowed on operator routes. Robot-specific device credentials are
// allowed only on deviceRoutes; mutating device routes explicitly reject an
// operator token so a human session cannot impersonate a robot.
func (a *Authenticator) RequireAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		if path == "/healthz" || path == "/v1/auth/login" || path == "/v1/world/events/ws" {
			next.ServeHTTP(w, r)
			return
		}
		if a.isStaticAsset(path) && r.URL.RawPath == "" {
			next.ServeHTTP(w, r)
			return
		}
		deviceRoute, isDeviceRoute := matchDeviceRoute(r.Method, path)
		robotID := strings.TrimSpace(r.Header.Get(deviceRobotHeader))
		deviceToken := r.Header.Get(deviceHeader)
		if robotID != "" || deviceToken != "" {
			if !a.DeviceTokenValid(r.Context(), robotID, deviceToken) {
				writeError(w, http.StatusUnauthorized, "DEVICE_CREDENTIAL_INVALID", "robot-specific device credential is invalid")
				return
			}
			if !isDeviceRoute {
				writeError(w, http.StatusForbidden, "DEVICE_ROUTE_DENIED", "device credential is not allowed on this route")
				return
			}
			ctx := context.WithValue(r.Context(), principalKey{}, Principal{Role: "device", Subject: robotID, RobotID: robotID})
			next.ServeHTTP(w, r.WithContext(ctx))
			return
		}
		if token := bearerToken(r); token != "" {
			if subject, err := a.VerifyToken(r.Context(), token); err == nil {
				if isDeviceRoute && deviceRoute.deviceOnly {
					writeError(w, http.StatusForbidden, "DEVICE_PRINCIPAL_REQUIRED", "this route requires a robot device principal")
					return
				}
				ctx := context.WithValue(r.Context(), principalKey{}, Principal{Role: "operator", Subject: subject})
				next.ServeHTTP(w, r.WithContext(ctx))
				return
			}
		}
		writeError(w, http.StatusUnauthorized, "UNAUTHORIZED", "operator or device authentication required")
	})
}

func matchDeviceRoute(method, path string) (deviceRoute, bool) {
	for _, route := range deviceRoutes {
		if route.method == method && matches(route.pattern, path) {
			return route, true
		}
	}
	return deviceRoute{}, false
}

func parseDeviceCredentials(raw string) (map[string]string, error) {
	result := map[string]string{}
	for _, pair := range strings.Split(raw, ",") {
		pair = strings.TrimSpace(pair)
		if pair == "" {
			continue
		}
		robotID, token, ok := strings.Cut(pair, ":")
		robotID, token = strings.TrimSpace(robotID), strings.TrimSpace(token)
		if !ok || robotID == "" || token == "" {
			return nil, errors.New("FLEET_DEVICE_CREDENTIALS must contain robot-id:token pairs")
		}
		if _, duplicate := result[robotID]; duplicate {
			return nil, errors.New("duplicate robot id in FLEET_DEVICE_CREDENTIALS")
		}
		result[robotID] = token
	}
	return result, nil
}

func cloneCredentials(source map[string]string) map[string]string {
	result := make(map[string]string, len(source))
	for robotID, token := range source {
		if strings.TrimSpace(robotID) != "" && token != "" {
			result[robotID] = token
		}
	}
	return result
}

func (a *Authenticator) isStaticAsset(path string) bool {
	switch path {
	case "/", "/index.html", "/app.js", "/world_view.js", "/webgl_scene.js", "/styles.css", "/favicon.ico":
		return true
	}
	return strings.HasPrefix(path, "/assets/") && pathpkg.Clean(path) == path
}

func bearerToken(r *http.Request) string {
	value := r.Header.Get(headerName)
	if strings.HasPrefix(value, "Bearer ") {
		return strings.TrimPrefix(value, "Bearer ")
	}
	return ""
}

// matches expands the "{id}"-style patterns used by http.ServeMux into a
// plain path prefix check. Only the segment count and exact static segments
// are compared; wildcard segments are ignored.
func matches(pattern, path string) bool {
	patternParts := strings.Split(strings.Trim(pattern, "/"), "/")
	pathParts := strings.Split(strings.Trim(path, "/"), "/")
	if len(patternParts) != len(pathParts) {
		return false
	}
	for i := range patternParts {
		if strings.HasPrefix(patternParts[i], "{") && strings.HasSuffix(patternParts[i], "}") {
			continue
		}
		if patternParts[i] != pathParts[i] {
			return false
		}
	}
	return true
}

// GenerateDeviceToken creates a strong random device token for provisioning.
func GenerateDeviceToken() (string, error) {
	random := make([]byte, 32)
	if _, err := rand.Read(random); err != nil {
		return "", err
	}
	return hex.EncodeToString(random), nil
}

// EnvOrDefaults renders the operator and robot-specific credential block for
// a .env file. Each configured robot receives an independent secret.
func EnvOrDefaults() (user, pass, credentials string, err error) {
	user = os.Getenv("FLEET_OPERATOR_USER")
	if user == "" {
		user = "admin"
	}
	pass = os.Getenv("FLEET_OPERATOR_PASSWORD")
	if pass == "" {
		pass = "admin123"
	}
	credentials = os.Getenv("FLEET_DEVICE_CREDENTIALS")
	if credentials == "" {
		robots := strings.Split(os.Getenv("FLEET_ROBOTS"), ",")
		if len(robots) == 1 && strings.TrimSpace(robots[0]) == "" {
			robots = []string{"robot-1", "robot-2"}
		}
		pairs := make([]string, 0, len(robots))
		for _, raw := range robots {
			robotID := strings.TrimSpace(raw)
			if robotID == "" {
				continue
			}
			token, generateErr := GenerateDeviceToken()
			if generateErr != nil {
				return "", "", "", generateErr
			}
			pairs = append(pairs, robotID+":"+token)
		}
		credentials = strings.Join(pairs, ",")
	}
	return user, pass, credentials, nil
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"code": code, "message": message})
}

// TokenInfo is the login response body.
type TokenInfo struct {
	Token     string `json:"token"`
	ExpiresAt int64  `json:"expiresAt"`
	Operator  string `json:"operator"`
}

// ExpiryLabel renders a human readable expiry for console display.
func ExpiryLabel(expiry time.Time) string {
	return strconv.FormatInt(expiry.UnixMilli(), 10)
}
