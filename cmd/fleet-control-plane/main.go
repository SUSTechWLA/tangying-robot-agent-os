package main

import (
	"context"
	"errors"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	llmagent "github.com/SUSTechWLA/tangying-robot-agent-os/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet"
	fleetmysql "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/mysql"
	fleetredis "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/redis"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func main() {
	listen := flag.String("listen", envOr("FLEET_LISTEN", ":8080"), "Fleet control-plane listen address")
	storeMode := flag.String("store", envOr("FLEET_STORE", "memory"), "memory or mysql")
	flag.Parse()
	if err := run(*listen, *storeMode); err != nil {
		log.Fatal(err)
	}
}

func run(listen, storeMode string) error {
	var repository tasks.Repository
	var closer func() error
	switch storeMode {
	case "memory":
		repository = tasks.NewMemoryStore()
		closer = func() error { return nil }
	case "mysql":
		store, err := fleetmysql.Open(envOr("MYSQL_DSN", ""))
		if err != nil {
			return err
		}
		repository = store
		closer = store.Close
	default:
		return errors.New("FLEET_STORE must be memory or mysql")
	}
	defer func() { _ = closer() }()

	parser := llmagent.NewParser(llmagent.Config{
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
	service := tasks.NewService(repository, parser, planner)

	var queue middleware.Queue[string]
	var queueCloser func() error
	redisAddr := os.Getenv("REDIS_ADDR")
	if redisAddr != "" {
		redisQueue, err := fleetredis.NewStreamQueue(
			redisAddr,
			os.Getenv("REDIS_PASSWORD"),
			envOr("REDIS_STREAM", "fleet.tasks.ready"),
			envOr("REDIS_GROUP", "fleet-control-plane"),
			"control-plane",
			0,
		)
		if err != nil {
			return err
		}
		if err := redisQueue.EnsureGroup(context.Background()); err != nil {
			redisQueue.Close()
			return err
		}
		queue = redisQueue
		queueCloser = redisQueue.Close
	} else {
		queue = memory.NewQueue[string](1024)
		queueCloser = queue.Close
	}
	defer func() { _ = queueCloser() }()

	server := &http.Server{
		Addr:              listen,
		Handler:           fleet.NewServer(service, queue).Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	errs := make(chan error, 1)
	go func() {
		log.Printf("fleet control plane listening on %s (store=%s)", listen, storeMode)
		errs <- server.ListenAndServe()
	}()
	select {
	case <-ctx.Done():
		shutdown, cancel := context.WithTimeout(context.Background(), 8*time.Second)
		defer cancel()
		return server.Shutdown(shutdown)
	case err := <-errs:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	}
}

func envOr(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
