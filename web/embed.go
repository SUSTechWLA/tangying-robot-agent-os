package web

import (
	"embed"
	"io/fs"
	"net/http"
	"path"
	"regexp"
	"strings"
)

//go:embed index.html app.js console_ui.js world_view.js webgl_scene.js navigation_view.js task_trace.js onboarding.js calibration.js styles.css assets
var assets embed.FS

var sha256Pattern = regexp.MustCompile(`^[a-f0-9]{64}$`)

func Handler() http.Handler {
	root, err := fs.Sub(assets, ".")
	if err != nil {
		panic(err)
	}
	files := http.FileServer(http.FS(root))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/assets/") && !isCleanAssetPath(r) {
			http.NotFound(w, r)
			return
		}
		if path.Ext(r.URL.Path) == ".glb" {
			w.Header().Set("Content-Type", "model/gltf-binary")
		}
		w.Header().Set("Cache-Control", assetCacheControl(r))
		files.ServeHTTP(w, r)
	})
}

func assetCacheControl(r *http.Request) string {
	if strings.HasPrefix(r.URL.Path, "/assets/") && sha256Pattern.MatchString(r.URL.Query().Get("v")) {
		return "public, max-age=31536000, immutable"
	}
	return "no-cache"
}

// isCleanAssetPath rejects both dot-segment traversal and encoded paths.
// The shipped asset names use only unescaped URL path characters, so rejecting
// RawPath keeps an upstream URL normalizer from turning an encoded path into a
// public asset route after this check.
func isCleanAssetPath(r *http.Request) bool {
	return r.URL.RawPath == "" && path.Clean(r.URL.Path) == r.URL.Path
}
