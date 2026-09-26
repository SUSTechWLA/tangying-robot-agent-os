// Package securehttp keeps requests carrying model credentials or robot
// observations at the endpoint selected by the operator.
package securehttp

import "net/http"

// NoRedirect copies a client so its transport and timeout remain intact while
// redirects return their 3xx response. Never mutate a caller-owned client:
// other users of that client may rely on different redirect behavior.
func NoRedirect(client *http.Client) *http.Client {
	if client == nil {
		client = http.DefaultClient
	}
	copy := *client
	copy.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return &copy
}
