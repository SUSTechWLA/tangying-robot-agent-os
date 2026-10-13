package web

import (
	"mime"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// A working source-tree preview must not hide an omitted go:embed asset: this
// exercises the binary's real HTTP handler and the script order it publishes.
func TestSemanticMapEditorIsServedByEmbeddedConsole(t *testing.T) {
	handler := Handler()
	index := httptest.NewRecorder()
	handler.ServeHTTP(index, httptest.NewRequest(http.MethodGet, "/", nil))
	if index.Code != http.StatusOK {
		t.Fatalf("console page returned %d", index.Code)
	}
	markup := index.Body.String()
	services := strings.Index(markup, `<script src="./robot_services.js" defer></script>`)
	semantic := strings.Index(markup, `<script src="./semantic_map.js" defer></script>`)
	app := strings.Index(markup, `<script src="./app.js" defer></script>`)
	if services < 0 || semantic < 0 || app < 0 || !(services < semantic && semantic < app) {
		t.Fatal("embedded page must load robot services, semantic editor and app in order")
	}
	script := httptest.NewRecorder()
	handler.ServeHTTP(script, httptest.NewRequest(http.MethodGet, "/semantic_map.js", nil))
	if script.Code != http.StatusOK || !strings.Contains(script.Body.String(), "TangyingSemanticMap") {
		t.Fatalf("semantic editor script missing from published binary: status=%d", script.Code)
	}
	mediaType, _, err := mime.ParseMediaType(script.Header().Get("Content-Type"))
	if err != nil || (mediaType != "text/javascript" && mediaType != "application/javascript") {
		t.Fatalf("semantic editor cannot execute with content-type %q", script.Header().Get("Content-Type"))
	}
	if script.Header().Get("Cache-Control") != "no-cache" {
		t.Fatal("unversioned semantic editor must be revalidated after an upgrade")
	}
}
