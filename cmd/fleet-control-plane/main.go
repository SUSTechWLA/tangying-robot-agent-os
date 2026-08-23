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
	"strings"
	"syscall"
	"time"

	llmagent "github.com/SUSTechWLA/tangying-robot-agent-os/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/gateway"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/mysql"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/queue"
	fleetredis "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/redis"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/registry"
	fleettelemetry "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/worldhub"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	redisv9 "github.com/redis/go-redis/v9"
)

func main() {
	listen := flag.String("listen", envOr("FLEET_LISTEN", ":8080"), "Fleet control-plane HTTP listen address (internal only)")
	storeMode := flag.String("store", envOr("FLEET_STORE", "memory"), "memory or mysql")
	flag.Parse()
	if err := run(*listen, *storeMode); err != nil {
		log.Fatal(err)
	}
}

func run(listen, storeMode string) error {
	var repository tasks.Repository
	var coordinationStore eventlog.Store
	var closer func() error
	switch storeMode {
	case "memory":
		repository = tasks.NewMemoryStore()
		coordinationStore = eventlog.NewMemoryStore()
		closer = func() error { return nil }
	case "mysql":
		store, err := mysql.Open(envOr("MYSQL_DSN", ""))
		if err != nil {
			return err
		}
		repository = store
		coordinationStore = store
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

	// Device registry and telemetry sink: Redis when available, in-memory
	// otherwise (single-instance dev profile).
	redisAddr := os.Getenv("REDIS_ADDR")
	var deviceRegistry *registry.Registry
	var telemetryStore fleettelemetry.Store
	var redisClosers []func() error
	if redisAddr != "" {
		redisRegistry := fleetredis.NewRegistryStore(redisAddr, os.Getenv("REDIS_PASSWORD"), 0)
		redisTelemetry := fleetredis.NewTelemetryStore(redisAddr, os.Getenv("REDIS_PASSWORD"), 0)
		deviceRegistry = registry.New(redisRegistry)
		telemetryStore = redisTelemetry
		redisClosers = append(redisClosers, redisRegistry.Close, redisTelemetry.Close)
		defer func() {
			for _, closer := range redisClosers {
				_ = closer()
			}
		}()
	} else {
		deviceRegistry = registry.New(registry.NewMemoryStore())
		telemetryStore = fleettelemetry.NewMemoryStore()
	}

	// Per-robot ready queues: Redis Streams (one stream per robot plus the
	// shared "any" stream) or in-memory queues.
	queueRouter := queue.NewRouter(time.Second)
	robotIDs := robotList()
	if redisAddr != "" {
		stream := envOr("REDIS_STREAM", "fleet.tasks.ready")
		group := envOr("REDIS_GROUP", "fleet-control-plane")
		for _, robotID := range append(append([]string(nil), robotIDs...), queue.AnyRobot) {
			redisQueue, err := fleetredis.NewStreamQueue(redisAddr, os.Getenv("REDIS_PASSWORD"),
				streamFor(stream, robotID), group, "control-plane-"+robotName(robotID), 0)
			if err != nil {
				return err
			}
			if err := redisQueue.EnsureGroup(context.Background()); err != nil {
				redisQueue.Close()
				return err
			}
			queueRouter.Add(robotID, redisQueue)
		}
	} else {
		for _, robotID := range append(append([]string(nil), robotIDs...), queue.AnyRobot) {
			queueRouter.Add(robotID, memory.NewQueue[string](1024))
		}
	}

	// Operator/device authentication.
	authenticator, err := auth.New(auth.Options{})
	if err != nil {
		return err
	}

	worldID := envOr("FLEET_WORLD_ID", "fleet-default")
	worldFreshness, _ := time.ParseDuration(envOr("FLEET_WORLD_FRESHNESS", "1s"))
	deltaRetention, _ := strconv.Atoi(envOr("FLEET_WORLD_DELTA_RETENTION", "512"))
	world := worldhub.New(worldID, worldFreshness, deltaRetention)

	claimLease, _ := time.ParseDuration(envOr("FLEET_INTENT_LEASE", "2m"))
	resourceLease, _ := time.ParseDuration(envOr("FLEET_RESOURCE_LEASE", "2m"))
	var leaderManager lease.Manager = lease.NewMemoryManager()
	var leaderRedis *redisv9.Client
	if redisAddr != "" {
		leaderRedis = redisv9.NewClient(&redisv9.Options{Addr: redisAddr, Password: os.Getenv("REDIS_PASSWORD")})
		defer leaderRedis.Close()
		leaderManager = fleetredis.NewLeaseManager(leaderRedis, "tangying:fleet")
	}
	handoffMaxAge, _ := time.ParseDuration(envOr("FLEET_HANDOFF_MAX_AGE", "5s"))
	taskCoordinator := coordinator.NewWithStore(service, claimLease, coordinationStore).
		WithEnqueue(queueRouter.Enqueue).
		WithWorld(world, handoffMaxAge).
		WithResourceLeases(leaderManager, resourceLease).
		WithCatalogLookup(func(ctx context.Context, robotID string) (string, error) {
			device, ok, err := deviceRegistry.Status(ctx, robotID)
			if err != nil {
				return "", err
			}
			if !ok || !device.Online {
				return "", errors.New("robot is offline or not registered: " + robotID)
			}
			if device.ToolCatalogRevision == "" {
				return "", errors.New("robot tool catalog revision is missing: " + robotID)
			}
			return device.ToolCatalogRevision, nil
		})
	leaderTTL, _ := time.ParseDuration(envOr("FLEET_LEADER_LEASE", "15s"))
	if err := taskCoordinator.WithLeadership(context.Background(), leaderManager, worldID, envOr("FLEET_COORDINATOR_ID", "control-plane-1"), leaderTTL); err != nil {
		return err
	}
	leaderStop := make(chan struct{})
	defer close(leaderStop)
	go func() {
		interval := leaderTTL / 3
		if interval <= 0 {
			interval = time.Second
		}
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-leaderStop:
				return
			case <-ticker.C:
				if err := taskCoordinator.RenewLeadership(context.Background(), leaderTTL); err != nil {
					log.Printf("fleet coordinator leadership renewal failed: %v", err)
				}
			}
		}
	}()
	go func() {
		ticker := time.NewTicker(500 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-leaderStop:
				return
			case <-ticker.C:
				if _, err := taskCoordinator.DispatchOutbox(context.Background(), 64); err != nil {
					log.Printf("fleet outbox dispatch deferred: %v", err)
				}
			}
		}
	}()

	// mTLS gRPC gateway for robot public access (heartbeat/lease/telemetry
	// streaming + server commands). Disabled when no certs are configured.
	deviceGateway, err := buildGateway(deviceRegistry, telemetryStore, service, world)
	if err != nil {
		return err
	}
	if deviceGateway != nil {
		go func() {
			gatewayListen := envOr("FLEET_GATEWAY_LISTEN", ":8443")
			if err := deviceGateway.Serve(gatewayListen); err != nil && !errors.Is(err, http.ErrServerClosed) {
				log.Printf("fleet gateway stopped: %v", err)
			}
		}()
	}

	server := fleet.NewServer(service, queueRouter,
		fleet.WithAuthenticator(authenticator),
		fleet.WithRegistry(deviceRegistry),
		fleet.WithTelemetry(telemetryStore),
		fleet.WithCoordinator(taskCoordinator),
		fleet.WithGateway(deviceGateway),
		fleet.WithWorld(world),
		fleet.WithAcceptanceNonce(os.Getenv("FLEET_ACCEPTANCE_NONCE")),
	)
	httpServer := &http.Server{
		Addr:              listen,
		Handler:           server.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	errs := make(chan error, 1)
	go func() {
		log.Printf("fleet control plane listening on %s (store=%s, robots=%s)", listen, storeMode, strings.Join(robotIDs, ","))
		errs <- httpServer.ListenAndServe()
	}()
	select {
	case <-ctx.Done():
		shutdown, shutdownCancel := context.WithTimeout(context.Background(), 8*time.Second)
		defer shutdownCancel()
		return httpServer.Shutdown(shutdown)
	case err := <-errs:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	}
}

func buildGateway(deviceRegistry *registry.Registry, telemetryStore fleettelemetry.Store, service *tasks.Service, world *worldhub.Hub) (*gateway.Server, error) {
	caFile := os.Getenv("FLEET_GRPC_CA")
	certFile := os.Getenv("FLEET_GRPC_CERT")
	keyFile := os.Getenv("FLEET_GRPC_KEY")
	if caFile == "" && certFile == "" && keyFile == "" {
		log.Printf("fleet gateway disabled: set FLEET_GRPC_CA/CERT/KEY for mTLS robot access")
		return nil, nil
	}
	requireCN, _ := strconv.ParseBool(envOr("FLEET_GRPC_REQUIRE_CN", "true"))
	lease, _ := time.ParseDuration(envOr("FLEET_DEVICE_LEASE", "15s"))
	heartbeat, _ := time.ParseDuration(envOr("FLEET_HEARTBEAT_INTERVAL", "5s"))
	return gateway.New(gateway.Options{
		CAFile: caFile, CertFile: certFile, KeyFile: keyFile,
		Registry: deviceRegistry, Telemetry: telemetryStore, Tasks: service, World: world,
		Lease: lease, HeartbeatInterval: heartbeat, RequireCNMatch: requireCN,
	})
}

func robotList() []string {
	raw := os.Getenv("FLEET_ROBOTS")
	if raw == "" {
		return []string{"robot-1", "robot-2"}
	}
	var robots []string
	for _, part := range strings.Split(raw, ",") {
		if robotID := strings.TrimSpace(part); robotID != "" {
			robots = append(robots, robotID)
		}
	}
	return robots
}

func streamFor(stream, robotID string) string {
	if robotID == "" {
		return stream + ".any"
	}
	return stream + "." + robotID
}

func robotName(robotID string) string {
	if robotID == "" {
		return "any"
	}
	return strings.ReplaceAll(robotID, "-", "")
}

func envOr(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
