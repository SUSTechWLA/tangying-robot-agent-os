package main

import (
	"context"
	"fmt"
	"os"

	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/robotagent"
)

var version = "dev"

func main() {
	app := robotagent.DefaultApp(version, os.Stdout, os.Stderr)
	if err := app.Run(context.Background(), os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}
