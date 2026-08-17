package web

import (
	"embed"
	"io/fs"
	"net/http"
)

//go:embed index.html app.js styles.css
var assets embed.FS

func Handler() http.Handler {
	root, err := fs.Sub(assets, ".")
	if err != nil {
		panic(err)
	}
	return http.FileServer(http.FS(root))
}
