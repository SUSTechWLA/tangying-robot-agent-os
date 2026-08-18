package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/api"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func main() {
	listen := flag.String("listen", ":8080", "HTTP listen address")
	dev := flag.Bool("dev", false, "use in-memory storage for local development")
	flag.Parse()

	ctx := context.Background()
	var store orchestrator.Store
	var closeStore func()
	if *dev {
		store = orchestrator.NewMemoryStore()
		closeStore = func() {}
	} else {
		databaseURL := os.Getenv("DATABASE_URL")
		if databaseURL == "" {
			log.Fatal("DATABASE_URL is required unless --dev is set")
		}
		postgres, err := orchestrator.NewPostgresStore(ctx, databaseURL)
		if err != nil {
			log.Fatalf("connect postgres: %v", err)
		}
		store = postgres
		closeStore = postgres.Close
	}
	defer closeStore()

	parser := agent.NewParser(agent.Config{
		Provider: os.Getenv("AGENT_PROVIDER"),
		BaseURL:  os.Getenv("AGENT_BASE_URL"),
		APIKey:   os.Getenv("AGENT_API_KEY"),
		Model:    os.Getenv("AGENT_MODEL"),
	})
	samples, _ := strconv.Atoi(os.Getenv("AGENT_ORCHESTRATION_SAMPLES"))
	planner := orchestration.New(manipulation.Catalog(), orchestration.Config{
		Provider: os.Getenv("AGENT_PROVIDER"),
		BaseURL:  os.Getenv("AGENT_BASE_URL"),
		APIKey:   os.Getenv("AGENT_API_KEY"),
		Model:    os.Getenv("AGENT_MODEL"),
		Samples:  samples,
	})
	service := orchestrator.NewService(store, parser, planner)
	server := &http.Server{Addr: *listen, Handler: api.NewServer(service).Handler(), ReadHeaderTimeout: 5_000_000_000}
	fmt.Printf("cloud control plane listening on %s\n", *listen)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
