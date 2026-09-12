package console

import (
	"encoding/json"
	"errors"
	"io/fs"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
)

// MapRootEnv names the directory holding built maps. Each map is a subdirectory
// containing the manifest and the artifacts it declares.
const MapRootEnv = "TANGYING_MAP_ROOT"

func mapRoot() string {
	if root := os.Getenv(MapRootEnv); root != "" {
		return root
	}
	return filepath.Join("artifacts", "maps")
}

// safeMapID accepts exactly what the manifest contract accepts: one path segment.
// The manifest already refuses hrefs that escape its directory; this refuses an
// id that escapes the map root, so neither layer has to trust the other.
func safeMapID(id string) bool {
	if id == "" || id == "." || id == ".." || len(id) > 128 {
		return false
	}
	if strings.ContainsAny(id, `/\`) {
		return false
	}
	first := id[0]
	alphanumeric := (first >= 'a' && first <= 'z') || (first >= 'A' && first <= 'Z') || (first >= '0' && first <= '9')
	return alphanumeric
}

// mapDir resolves a map id under the root, refusing anything that leaves it.
func mapDir(root, id string) (string, bool) {
	if !safeMapID(id) {
		return "", false
	}
	directory := filepath.Join(root, id)
	relative, err := filepath.Rel(root, directory)
	if err != nil || strings.HasPrefix(relative, "..") || filepath.IsAbs(relative) {
		return "", false
	}
	return directory, true
}

type mapSummary struct {
	MapID        string  `json:"mapId"`
	RobotID      string  `json:"robotId"`
	FrameID      string  `json:"frameId"`
	Source       string  `json:"source"`
	Mode         string  `json:"mode"`
	CreatedAtMS  int64   `json:"createdAtUnixMs"`
	PointCount   int64   `json:"pointCount"`
	LODLevels    int64   `json:"lodLevels"`
	Bounds       any     `json:"bounds"`
	CloudBytes   int64   `json:"cloudBytes"`
	GridBytes    int64   `json:"gridBytes"`
	Calibration  string  `json:"calibrationRevision"`
	ManifestHash string  `json:"hash"`
	Directory    string  `json:"-"`
	ModifiedAt   float64 `json:"-"`
}

// listMaps reads every manifest under the root. A map whose manifest is missing
// or unreadable is skipped rather than failing the whole listing: one broken map
// must not hide the others.
func listMaps(root string) []mapSummary {
	entries, err := os.ReadDir(root)
	if err != nil {
		return []mapSummary{}
	}
	summaries := make([]mapSummary, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() || !safeMapID(entry.Name()) {
			continue
		}
		summary, err := readMapSummary(filepath.Join(root, entry.Name()))
		if err != nil {
			continue
		}
		summaries = append(summaries, summary)
	}
	sort.Slice(summaries, func(i, j int) bool {
		return summaries[i].CreatedAtMS > summaries[j].CreatedAtMS
	})
	return summaries
}

func readMapSummary(directory string) (mapSummary, error) {
	raw, err := os.ReadFile(filepath.Join(directory, "manifest.json"))
	if err != nil {
		return mapSummary{}, err
	}
	var document map[string]any
	if err := json.Unmarshal(raw, &document); err != nil {
		return mapSummary{}, err
	}
	artifacts, _ := document["artifacts"].(map[string]any)
	bytesOf := func(role string) int64 {
		entry, _ := artifacts[role].(map[string]any)
		value, _ := entry["bytes"].(float64)
		return int64(value)
	}
	number := func(key string) int64 {
		value, _ := document[key].(float64)
		return int64(value)
	}
	text := func(key string) string {
		value, _ := document[key].(string)
		return value
	}
	summary := mapSummary{
		MapID: text("mapId"), RobotID: text("robotId"), FrameID: text("frameId"),
		Source: text("source"), Mode: text("mode"), Bounds: document["bounds"],
		CreatedAtMS: number("createdAtUnixMs"), PointCount: number("pointCount"),
		LODLevels: number("lodLevels"), CloudBytes: bytesOf("cloud"), GridBytes: bytesOf("grid"),
		Calibration: text("calibrationRevision"), ManifestHash: text("hash"),
	}
	if info, err := os.Stat(filepath.Join(directory, "manifest.json")); err == nil {
		summary.ModifiedAt = float64(info.ModTime().UnixMilli())
	}
	return summary, nil
}

// mapsRoutes registers the map endpoints. Read-only: building a map is a pipeline
// concern, and the console has no business deleting somebody's survey.
func (s *Server) registerMapRoutes() {
	s.mux.HandleFunc("GET /v1/maps", s.listMaps)
	s.mux.HandleFunc("GET /v1/maps/{id}", s.getMapManifest)
	s.mux.HandleFunc("GET /v1/maps/{id}/cloud", s.getMapCloud)
	s.mux.HandleFunc("GET /v1/maps/{id}/artifact/{role}", s.getMapArtifact)
}

func (s *Server) listMaps(w http.ResponseWriter, r *http.Request) {
	root := mapRoot()
	summaries := listMaps(root)
	writeJSON(w, http.StatusOK, map[string]any{
		"maps":  summaries,
		"count": len(summaries),
		"root":  root,
	})
}

func (s *Server) getMapManifest(w http.ResponseWriter, r *http.Request) {
	root := mapRoot()
	directory, ok := mapDir(root, r.PathValue("id"))
	if !ok {
		writeError(w, http.StatusBadRequest, "INVALID_MAP_ID", "map id must be a single safe path segment")
		return
	}
	raw, err := os.ReadFile(filepath.Join(directory, "manifest.json"))
	if errors.Is(err, fs.ErrNotExist) {
		writeError(w, http.StatusNotFound, "MAP_NOT_FOUND", "no map with that id")
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "MAP_UNREADABLE", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_, _ = w.Write(raw)
}

// serveMapFile is the one place a map artifact leaves the machine. Range support
// comes from http.ServeContent, and it is the whole reason the client can pull a
// level of detail instead of the entire cloud.
func (s *Server) serveMapFile(w http.ResponseWriter, r *http.Request, role string) {
	root := mapRoot()
	directory, ok := mapDir(root, r.PathValue("id"))
	if !ok {
		writeError(w, http.StatusBadRequest, "INVALID_MAP_ID", "map id must be a single safe path segment")
		return
	}
	manifest, err := os.ReadFile(filepath.Join(directory, "manifest.json"))
	if err != nil {
		writeError(w, http.StatusNotFound, "MAP_NOT_FOUND", "no map with that id")
		return
	}
	var document struct {
		Artifacts map[string]struct {
			Href string `json:"href"`
		} `json:"artifacts"`
	}
	if err := json.Unmarshal(manifest, &document); err != nil {
		writeError(w, http.StatusInternalServerError, "MAP_MALFORMED", err.Error())
		return
	}
	entry, ok := document.Artifacts[role]
	if !ok {
		writeError(w, http.StatusNotFound, "ARTIFACT_NOT_DECLARED", "this map declares no "+role+" artifact")
		return
	}
	// The href was validated when the manifest was written, and it is re-checked
	// here: a manifest that arrived from elsewhere must not be able to address a
	// file outside its own directory.
	clean := path.Clean(entry.Href)
	if clean != entry.Href || strings.HasPrefix(clean, "..") || path.IsAbs(clean) {
		writeError(w, http.StatusForbidden, "ARTIFACT_ESCAPES_MAP", "artifact path leaves the map directory")
		return
	}
	target := filepath.Join(directory, filepath.FromSlash(clean))
	file, err := os.Open(target)
	if errors.Is(err, fs.ErrNotExist) {
		writeError(w, http.StatusNotFound, "ARTIFACT_MISSING", "the manifest declares a file that is not there")
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "ARTIFACT_UNREADABLE", err.Error())
		return
	}
	defer func() { _ = file.Close() }()
	info, err := file.Stat()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "ARTIFACT_UNREADABLE", err.Error())
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	// ServeContent answers Range requests itself, including 206 and Content-Range.
	// That is what lets a client pull one level of detail rather than the whole
	// cloud, so it is the reason this route exists at all.
	http.ServeContent(w, r, filepath.Base(target), info.ModTime(), file)
}

func (s *Server) getMapCloud(w http.ResponseWriter, r *http.Request) {
	s.serveMapFile(w, r, "cloud")
}

func (s *Server) getMapArtifact(w http.ResponseWriter, r *http.Request) {
	s.serveMapFile(w, r, r.PathValue("role"))
}
