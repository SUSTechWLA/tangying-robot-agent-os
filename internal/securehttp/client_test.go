package securehttp

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestNoRedirectKeepsBearerAtConfiguredEndpoint(t *testing.T) {
	var redirected bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/unexpected" {
			redirected = true
			return
		}
		if r.Header.Get("Authorization") != "Bearer secret" {
			t.Errorf("configured endpoint did not receive its credential")
		}
		http.Redirect(w, r, "/unexpected", http.StatusTemporaryRedirect)
	}))
	defer server.Close()

	base := server.Client()
	client := NoRedirect(base)
	request, err := http.NewRequest(http.MethodPost, server.URL+"/chat/completions", nil)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Authorization", "Bearer secret")
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusTemporaryRedirect || redirected {
		t.Fatalf("redirect status=%d followed=%t", response.StatusCode, redirected)
	}
	if base.CheckRedirect != nil {
		t.Fatal("caller-owned client was modified")
	}
}
