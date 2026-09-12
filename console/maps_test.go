package console_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
)

// buildMapFixture writes a minimal but honest map directory: a manifest that
// declares a cloud and a grid, and the files themselves.
func buildMapFixture(t *testing.T, root, id string, cloud []byte) {
	t.Helper()
	directory := filepath.Join(root, id)
	if err := os.MkdirAll(filepath.Join(directory, "cloud"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "cloud", "lod4.bin"), cloud, 0o644); err != nil {
		t.Fatal(err)
	}
	grid := []byte("\x89PNG\r\n\x1a\nnot-really-a-png-but-present")
	if err := os.WriteFile(filepath.Join(directory, "grid.png"), grid, 0o644); err != nil {
		t.Fatal(err)
	}
	manifest := map[string]any{
		"schemaVersion": "map.manifest.v1", "mapId": id, "robotId": "xlerobot-01",
		"frameId": "map", "createdAtUnixMs": 1789000000000, "source": "rtabmap",
		"mode": "mapping", "pointCount": 348669, "lodLevels": 5,
		"floors": []any{map[string]any{"id": "ground", "zMin": -0.3, "zMax": 2.7}},
		"bounds": map[string]any{"min": []float64{-3, -2, -0.2}, "max": []float64{3, 8, 2.6}},
		"artifacts": map[string]any{
			"cloud": map[string]any{"href": "cloud/lod4.bin", "bytes": len(cloud), "sha256": strings.Repeat("a", 64)},
			"grid":  map[string]any{"href": "grid.png", "bytes": len(grid), "sha256": strings.Repeat("b", 64)},
		},
		"calibrationRevision": strings.Repeat("c", 64), "hash": strings.Repeat("d", 64),
	}
	raw, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
}

func mapServer(t *testing.T, root string) http.Handler {
	t.Helper()
	t.Setenv(console.MapRootEnv, root)
	return console.NewServer(nil, nil).Handler()
}

func TestMapListingReportsWhatIsThere(t *testing.T) {
	root := t.TempDir()
	buildMapFixture(t, root, "home-loadtest", make([]byte, 4096))
	buildMapFixture(t, root, "kitchen-survey", make([]byte, 2048))
	// A directory with no manifest must not break the listing, and must not be
	// offered as a map either.
	if err := os.MkdirAll(filepath.Join(root, "half-written"), 0o755); err != nil {
		t.Fatal(err)
	}

	response := httptest.NewRecorder()
	mapServer(t, root).ServeHTTP(response, httptest.NewRequest("GET", "/v1/maps", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("%d %s", response.Code, response.Body.String())
	}
	var payload struct {
		Count int `json:"count"`
		Maps  []struct {
			MapID      string `json:"mapId"`
			PointCount int64  `json:"pointCount"`
			CloudBytes int64  `json:"cloudBytes"`
			LODLevels  int64  `json:"lodLevels"`
		} `json:"maps"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.Count != 2 {
		t.Fatalf("expected the two built maps, got %d: %s", payload.Count, response.Body.String())
	}
	ids := map[string]bool{}
	for _, entry := range payload.Maps {
		ids[entry.MapID] = true
		if entry.PointCount != 348669 || entry.LODLevels != 5 {
			t.Fatalf("manifest fields did not survive the listing: %+v", entry)
		}
	}
	if !ids["home-loadtest"] || !ids["kitchen-survey"] {
		t.Fatalf("missing maps: %v", ids)
	}
}

func TestMapCloudAnswersRangeRequests(t *testing.T) {
	// This is the point of the whole route. COPC-style level-of-detail loading
	// depends on the server honouring Range; if it returns 200 with the whole
	// file, every client silently downloads the entire cloud and the LOD design
	// buys nothing.
	root := t.TempDir()
	cloud := make([]byte, 10000)
	for index := range cloud {
		cloud[index] = byte(index % 251)
	}
	buildMapFixture(t, root, "range-test", cloud)

	request := httptest.NewRequest("GET", "/v1/maps/range-test/cloud", nil)
	request.Header.Set("Range", "bytes=100-199")
	response := httptest.NewRecorder()
	mapServer(t, root).ServeHTTP(response, request)

	if response.Code != http.StatusPartialContent {
		t.Fatalf("expected 206 for a range request, got %d", response.Code)
	}
	body := response.Body.Bytes()
	if len(body) != 100 {
		t.Fatalf("expected 100 bytes, got %d", len(body))
	}
	if string(body) != string(cloud[100:200]) {
		t.Fatal("the returned bytes are not the requested slice")
	}
	if got := response.Header().Get("Content-Range"); got != "bytes 100-199/10000" {
		t.Fatalf("Content-Range was %q", got)
	}
	if got := response.Header().Get("Accept-Ranges"); got != "bytes" {
		t.Fatalf("Accept-Ranges was %q", got)
	}
}

func TestMapCloudWithoutARangeIsTheWholeFile(t *testing.T) {
	root := t.TempDir()
	buildMapFixture(t, root, "whole", make([]byte, 512))
	response := httptest.NewRecorder()
	mapServer(t, root).ServeHTTP(response, httptest.NewRequest("GET", "/v1/maps/whole/cloud", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("%d", response.Code)
	}
	if response.Body.Len() != 512 {
		t.Fatalf("expected the whole file, got %d bytes", response.Body.Len())
	}
}

func TestMapIDsThatCouldEscapeTheRootAreRefused(t *testing.T) {
	root := t.TempDir()
	// A secret outside the map root that a traversal would reach.
	secret := filepath.Join(filepath.Dir(root), "secret.json")
	if err := os.WriteFile(secret, []byte("do-not-serve"), 0o644); err != nil {
		t.Fatal(err)
	}
	handler := mapServer(t, root)
	for _, id := range []string{"..", "../secret.json", ".", "a/b"} {
		for _, suffix := range []string{"", "/cloud"} {
			response := httptest.NewRecorder()
			// Use the raw path so the traversal reaches the handler rather than
			// being normalised away by the request builder.
			handler.ServeHTTP(response, httptest.NewRequest("GET", "/v1/maps/"+id+suffix, nil))
			if response.Code == http.StatusOK {
				t.Fatalf("id %q was served with %d", id, response.Code)
			}
			if strings.Contains(response.Body.String(), "do-not-serve") {
				t.Fatalf("id %q escaped the map root", id)
			}
		}
	}
}

func TestAManifestDeclaringAPathOutsideItsDirectoryIsRefused(t *testing.T) {
	// Defence in depth: the manifest contract already refuses such an href, but a
	// manifest can arrive from somewhere else, so the route checks again.
	root := t.TempDir()
	directory := filepath.Join(root, "sneaky")
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(root, "outside.bin")
	if err := os.WriteFile(outside, []byte("secret"), 0o644); err != nil {
		t.Fatal(err)
	}
	manifest := `{"artifacts":{"cloud":{"href":"../outside.bin","bytes":6,"sha256":"` +
		strings.Repeat("a", 64) + `"}}}`
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), []byte(manifest), 0o644); err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	mapServer(t, root).ServeHTTP(response, httptest.NewRequest("GET", "/v1/maps/sneaky/cloud", nil))
	if response.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for an escaping href, got %d %s", response.Code, response.Body.String())
	}
	if strings.Contains(response.Body.String(), "secret") {
		t.Fatal("the file outside the map directory was served")
	}
}

func TestAnUnknownMapOrRoleIsANotFoundNotAnEmptySuccess(t *testing.T) {
	root := t.TempDir()
	buildMapFixture(t, root, "present", make([]byte, 64))
	handler := mapServer(t, root)
	for path, expected := range map[string]int{
		"/v1/maps/absent":                 http.StatusNotFound,
		"/v1/maps/absent/cloud":           http.StatusNotFound,
		"/v1/maps/present":                http.StatusOK,
		"/v1/maps/present/artifact/mesh":  http.StatusNotFound,
		"/v1/maps/present/artifact/grid":  http.StatusOK,
		"/v1/maps/present/artifact/cloud": http.StatusOK,
	} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest("GET", path, nil))
		if response.Code != expected {
			t.Fatalf("%s: expected %d, got %d %s", path, expected, response.Code, response.Body.String())
		}
	}
}

func TestAnEmptyMapRootIsAnEmptyListNotAFailure(t *testing.T) {
	response := httptest.NewRecorder()
	mapServer(t, filepath.Join(t.TempDir(), "does-not-exist")).
		ServeHTTP(response, httptest.NewRequest("GET", "/v1/maps", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", response.Code)
	}
	if !strings.Contains(response.Body.String(), `"count":0`) {
		t.Fatalf("expected an empty list, got %s", response.Body.String())
	}
}

func TestCloudLevelsAreAddressableAndRangeChecked(t *testing.T) {
	// A client streaming a map wants level 0 first; it must not have to download
	// the finest level to discover that the coarse ones exist.
	root := t.TempDir()
	directory := filepath.Join(root, "levels")
	if err := os.MkdirAll(filepath.Join(directory, "cloud"), 0o755); err != nil {
		t.Fatal(err)
	}
	for level, size := range map[int]int{0: 128, 1: 256, 2: 512, 3: 1024, 4: 2048} {
		payload := make([]byte, size)
		for index := range payload {
			payload[index] = byte(level)
		}
		if err := os.WriteFile(filepath.Join(directory, "cloud", "lod"+strconv.Itoa(level)+".bin"), payload, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	manifest := map[string]any{
		"schemaVersion": "map.manifest.v1", "mapId": "levels", "robotId": "r", "frameId": "map",
		"createdAtUnixMs": 1, "source": "rtabmap", "mode": "mapping", "pointCount": 100,
		"lodLevels": 5,
		"floors":    []any{map[string]any{"id": "ground", "zMin": 0, "zMax": 2}},
		"bounds":    map[string]any{"min": []float64{0, 0, 0}, "max": []float64{1, 1, 1}},
		"artifacts": map[string]any{
			"cloud": map[string]any{"href": "cloud/lod4.bin", "bytes": 2048, "sha256": strings.Repeat("a", 64)},
			"grid":  map[string]any{"href": "cloud/lod0.bin", "bytes": 128, "sha256": strings.Repeat("b", 64)},
		},
		"calibrationRevision": strings.Repeat("c", 64), "hash": strings.Repeat("d", 64),
	}
	raw, _ := json.Marshal(manifest)
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}

	handler := mapServer(t, root)
	// Level 0 is the coarsest and must be small enough to draw immediately.
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "/v1/maps/levels/cloud?lod=0", nil))
	if response.Code != http.StatusOK || response.Body.Len() != 128 {
		t.Fatalf("lod=0: %d, %d bytes", response.Code, response.Body.Len())
	}
	if response.Body.Bytes()[0] != 0 {
		t.Fatal("lod=0 served the wrong file")
	}
	// And it ranges like any other artifact.
	request := httptest.NewRequest("GET", "/v1/maps/levels/cloud?lod=2", nil)
	request.Header.Set("Range", "bytes=0-9")
	ranged := httptest.NewRecorder()
	handler.ServeHTTP(ranged, request)
	if ranged.Code != http.StatusPartialContent || ranged.Body.Len() != 10 {
		t.Fatalf("ranged lod=2: %d, %d bytes", ranged.Code, ranged.Body.Len())
	}

	for path, expected := range map[string]int{
		"/v1/maps/levels/cloud?lod=5":   http.StatusNotFound, // beyond lodLevels
		"/v1/maps/levels/cloud?lod=-1":  http.StatusBadRequest,
		"/v1/maps/levels/cloud?lod=abc": http.StatusBadRequest,
	} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest("GET", path, nil))
		if response.Code != expected {
			t.Fatalf("%s: expected %d, got %d", path, expected, response.Code)
		}
	}
}
