package console

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"mime"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// The write guard.
//
// Two things had to be true before a mutating console request could be read as
// "a person decided this", and neither was.
//
//  1. It had to come from this console rather than from any page the operator
//     happened to have open. The console serves JSON with no CORS headers, so a
//     foreign page cannot read a response — but a cross-origin POST carrying a
//     CORS-safelisted content type is delivered with no preflight, and every
//     mutating route here accepted one. Only POST /v1/robot/services checked.
//
//  2. The approval had to be falsifiable. POST /v1/recovery/execute recorded
//     `OperatorApproved: true` next to a comment saying "a person called this
//     endpoint about this action. That is the approval" — while nothing in the
//     request distinguished a person from any other process that could reach the
//     port. The strongest reading of that field was "somebody called it".
//
// Mutating requests now carry a session token minted when the console starts:
// a SameSite=Strict cookie for the page, or the X-Tangying-Session header for
// tests and CLI tools. A cross-site request carries neither — SameSite=Strict
// keeps the cookie home, and a custom header needs a preflight this server never
// grants.
//
// This is deliberately not a user account. The console has no notion of who a
// person is, and inventing one here would be a weaker second identity system
// beside the Fleet's real auth. What it establishes is narrower and honest: the
// request came from a client that had loaded this console, in this process
// lifetime. That is what OperatorApproved is now allowed to mean, and it is
// recorded as such rather than relayed through a port that would answer "yes,
// because you asked".
//
// # How a local tool gets the token
//
// A browser gets it as a cookie and never sees it. A command-line client — the
// acceptance scripts, the curl examples in the docs — reads it from
// `<data dir>/console-session`, which the agent writes 0600 at startup
// (cmd/local-agent/main.go).
//
// That file is the same boundary the task database already has: anything that can
// read it can read every task's text, so it grants no new reach. What would grant
// new reach is an endpoint serving the token to whoever asks, and there is none:
// a token fetched by asking is a token that means "whoever asked".

// SessionCookieName is the cookie the console page carries. It is the browser's
// half of the session: the page needs no code to send it.
const SessionCookieName = "tangying_session"

// SessionHeaderName is the non-browser half, for tests and CLI tools.
const SessionHeaderName = "X-Tangying-Session"

// Refusal reasons for a refused write. They are stable because the console
// shows them and a test asserts on them.
const (
	// RefusalCrossSite means the request announced itself as coming from
	// another site, or from an origin that is not this host.
	RefusalCrossSite = "CONSOLE_CROSS_SITE_WRITE"
	// RefusalNoSession means the request carried no valid console session.
	RefusalNoSession = "CONSOLE_SESSION_REQUIRED"
	// RefusalNotJSON means a request with a body did not declare JSON.
	RefusalNotJSON = "CONSOLE_JSON_REQUIRED"
)

// sessionGuard holds this process's console session token. It lives for one
// process lifetime: a restart invalidates every cookie, which is the right
// scope for a value that answers "is this the console I am talking to".
type sessionGuard struct {
	token string
}

// newSessionGuard mints a token.
//
// A failure here is fatal rather than degraded. The alternative — start anyway
// with an empty token — would leave every write refused, and "the console
// loads but nothing works" is the kind of symptom that gets misdiagnosed as a
// UI bug for a week. A console that cannot mint a session cannot safely serve
// writes, so it does not start.
func newSessionGuard() *sessionGuard {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		panic(fmt.Sprintf("console session token: %v", err))
	}
	return &sessionGuard{token: base64.RawURLEncoding.EncodeToString(raw)}
}

// matches reports whether this request carries the console's session token.
//
// Comparison is constant time. The token is a bearer credential over loopback,
// where a timing side channel is a stretch, but the comparison costs nothing and
// an early-exit byte compare is the kind of thing that gets copied somewhere it
// does matter.
func (g *sessionGuard) matches(r *http.Request) bool {
	if g == nil || g.token == "" {
		// Fail closed: a guard with no token authorises nothing.
		return false
	}
	if subtle.ConstantTimeCompare([]byte(r.Header.Get(SessionHeaderName)), []byte(g.token)) == 1 {
		return true
	}
	cookie, err := r.Cookie(SessionCookieName)
	if err != nil {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(cookie.Value), []byte(g.token)) == 1
}

// ensureCookie hands the page its token, so the browser can call the console
// without the page knowing a token exists.
//
// Only GET is allowed to set it. A cookie set by a POST would be a cookie a
// cross-site request could plant — and the value would be wrong anyway, since
// the attacker cannot read the real one.
func (g *sessionGuard) ensureCookie(w http.ResponseWriter, r *http.Request) {
	if g == nil || g.token == "" || r.Method != http.MethodGet {
		return
	}
	if cookie, err := r.Cookie(SessionCookieName); err == nil && cookie.Value == g.token {
		return
	}
	http.SetCookie(w, &http.Cookie{
		Name:     SessionCookieName,
		Value:    g.token,
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteStrictMode,
		// No Secure flag: the local console is documented as loopback HTTP, and
		// a Secure cookie would simply never be sent there. If the console is
		// ever served over TLS this must be set from the request scheme rather
		// than left off.
	})
}

// allowOperatorWrite is the single gate every mutating handler passes through.
//
// It is one function on purpose. The defect it fixes was not a missing check in
// one handler: it was that eleven handlers each decided for themselves, and ten
// decided nothing.
func (s *Server) allowOperatorWrite(w http.ResponseWriter, r *http.Request) bool {
	if !sameOriginRequest(r) {
		writeError(w, http.StatusForbidden, RefusalCrossSite,
			"写操作必须来自本控制台页面。")
		return false
	}
	if !s.session.matches(r) {
		writeError(w, http.StatusUnauthorized, RefusalNoSession,
			"这个请求没有本控制台的会话。请在控制台页面里操作，或带上 "+SessionHeaderName+" 头。")
		return false
	}
	// A body without a JSON content type is the shape a cross-site form post
	// takes. Requests that legitimately have no body (approve, pause, cancel)
	// pass: the session token, not the content type, is what authorises them.
	if r.ContentLength != 0 {
		media, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if err != nil || media != "application/json" {
			writeError(w, http.StatusUnsupportedMediaType, RefusalNotJSON,
				"带请求体的写操作必须使用 application/json。")
			return false
		}
	}
	return true
}

// sameOriginRequest rejects a request that announces itself as coming from
// somewhere else.
//
// A missing Origin is allowed through here and stopped by the session check
// instead. Origin is a browser-only header, so requiring it would break curl and
// the CLI without adding anything: neither of those gets a session token for
// free either.
func sameOriginRequest(r *http.Request) bool {
	if strings.EqualFold(r.Header.Get("Sec-Fetch-Site"), "cross-site") {
		return false
	}
	origin := r.Header.Get("Origin")
	if origin == "" {
		return true
	}
	parsed, err := url.Parse(origin)
	if err != nil {
		return false
	}
	// "null" (sandboxed or file:// documents) parses to an empty host and is
	// refused by this comparison, which is the wanted outcome.
	return strings.EqualFold(parsed.Host, r.Host)
}

// operatorEvidence renders what authorises a write, for the durable record.
//
// It is a short printable scalar rather than a struct because it ends up in an
// event payload an operator reads, and because the sha256 prefix is enough to
// tell two sessions apart without putting the bearer token itself into a ledger
// that gets exported, copied into incident bundles and used as training data.
func (s *Server) operatorEvidence(r *http.Request) string {
	if s.session == nil || s.session.token == "" {
		return ""
	}
	digest := sha256.Sum256([]byte(s.session.token))
	parts := []string{"console-session:" + hex.EncodeToString(digest[:])[:12]}
	if host := strings.TrimSpace(r.RemoteAddr); host != "" {
		parts = append(parts, "from "+host)
	}
	parts = append(parts, "at "+time.Now().UTC().Format(time.RFC3339))
	return strings.Join(parts, " ")
}
