package auth

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func newTestAuthenticator(t *testing.T) *Authenticator {
	t.Helper()
	authenticator, err := New(Options{
		OperatorUser: "admin", OperatorPass: "admin123",
		DeviceCredentials: map[string]string{"robot-1": "device-token-1", "robot-2": "device-token-2"},
		Secret:            "test-secret",
	})
	if err != nil {
		t.Fatal(err)
	}
	return authenticator
}

func TestLoginIssuesVerifiableToken(t *testing.T) {
	authenticator := newTestAuthenticator(t)
	token, expiry, err := authenticator.Login(context.Background(), "admin", "admin123")
	if err != nil {
		t.Fatal(err)
	}
	if expiry.Before(time.Now().Add(20 * time.Hour)) {
		t.Fatalf("expiry = %v, want ~24h", expiry)
	}
	subject, err := authenticator.VerifyToken(context.Background(), token)
	if err != nil || subject != "admin" {
		t.Fatalf("verify = %q (err=%v)", subject, err)
	}
}

func TestLoginRejectsBadCredentials(t *testing.T) {
	authenticator := newTestAuthenticator(t)
	if _, _, err := authenticator.Login(context.Background(), "admin", "wrong"); err == nil {
		t.Fatal("wrong password must be rejected")
	}
	if _, _, err := authenticator.Login(context.Background(), "nobody", "admin123"); err == nil {
		t.Fatal("unknown user must be rejected")
	}
}

func TestTamperedTokenRejected(t *testing.T) {
	authenticator := newTestAuthenticator(t)
	token, _, err := authenticator.Login(context.Background(), "admin", "admin123")
	if err != nil {
		t.Fatal(err)
	}
	parts := strings.Split(token, ".")
	parts[1] = strings.ReplaceAll(parts[1], "A", "B")
	if _, err := authenticator.VerifyToken(context.Background(), strings.Join(parts, ".")); err == nil {
		t.Fatal("tampered token must be rejected")
	}
}

func TestExpiredTokenRejected(t *testing.T) {
	authenticator, err := New(Options{OperatorUser: "admin", OperatorPass: "admin123", Secret: "s"})
	if err != nil {
		t.Fatal(err)
	}
	token, _, err := authenticator.Login(context.Background(), "admin", "admin123")
	if err != nil {
		t.Fatal(err)
	}
	authenticator.now = func() time.Time { return time.Now().Add(25 * time.Hour) }
	if _, err := authenticator.VerifyToken(context.Background(), token); err == nil {
		t.Fatal("expired token must be rejected")
	}
}

func TestDeviceTokenAndRouteAllowlist(t *testing.T) {
	authenticator := newTestAuthenticator(t)
	next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	handler := authenticator.RequireAuth(next)

	// Device token allowed on device routes.
	for _, route := range []struct{ method, path string }{
		{http.MethodGet, "/v1/queue/next"},
		{http.MethodPost, "/v1/telemetry"},
		{http.MethodPost, "/v1/tasks/task-1/intents/next"},
		{http.MethodPost, "/v1/tasks/task-1/intents/0/complete"},
		{http.MethodGet, "/v1/tasks/task-1"},
	} {
		request := httptest.NewRequest(route.method, route.path, nil)
		request.Header.Set("X-Robot-ID", "robot-1")
		request.Header.Set("X-Device-Token", "device-token-1")
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, request)
		if recorder.Code != http.StatusOK {
			t.Fatalf("device route %s %s status = %d", route.method, route.path, recorder.Code)
		}
	}

	// Device token denied on console routes.
	request := httptest.NewRequest(http.MethodGet, "/v1/devices", nil)
	request.Header.Set("X-Robot-ID", "robot-1")
	request.Header.Set("X-Device-Token", "device-token-1")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("device token on console route status = %d, want 403", recorder.Code)
	}

	// A credential provisioned to robot-1 cannot be relabelled as robot-2.
	request = httptest.NewRequest(http.MethodGet, "/v1/queue/next", nil)
	request.Header.Set("X-Robot-ID", "robot-2")
	request.Header.Set("X-Device-Token", "device-token-1")
	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("cross-robot token status = %d, want 401", recorder.Code)
	}

	// No auth at all -> 401.
	request = httptest.NewRequest(http.MethodGet, "/v1/tasks", nil)
	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("anonymous status = %d, want 401", recorder.Code)
	}
}

func TestWorldSocketTicketIsShortLivedAndSingleUse(t *testing.T) {
	now := time.Unix(100, 0).UTC()
	authenticator, err := New(Options{
		OperatorUser: "admin", OperatorPass: "admin123",
		DeviceCredentials: map[string]string{"robot-1": "device-token"},
		Secret:            "test-secret", Now: func() time.Time { return now },
	})
	if err != nil {
		t.Fatal(err)
	}
	ticket, expiry, err := authenticator.IssueWSTicket(context.Background(), "admin", "fleet-world")
	if err != nil || ticket == "" || expiry.Sub(now) != WSTicketLifetime {
		t.Fatalf("ticket=%q expiry=%v err=%v", ticket, expiry, err)
	}
	if subject, err := authenticator.ConsumeWSTicket(context.Background(), ticket, "fleet-world"); err != nil || subject != "admin" {
		t.Fatalf("consume subject=%q err=%v", subject, err)
	}
	if _, err := authenticator.ConsumeWSTicket(context.Background(), ticket, "fleet-world"); !errors.Is(err, ErrInvalidWSTicket) {
		t.Fatalf("reused ticket err=%v", err)
	}
}

func TestWorldViewModuleIsPublicStaticAsset(t *testing.T) {
	authenticator := newTestAuthenticator(t)
	handler := authenticator.RequireAuth(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	request := httptest.NewRequest(http.MethodGet, "/world_view.js", nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("world module status=%d", recorder.Code)
	}
}
