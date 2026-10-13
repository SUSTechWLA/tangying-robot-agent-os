package main

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

type runtimeOptions struct {
	runtimeInsecure                                                             bool
	startupTimeout, controlTimeout, telemetryInterval, heartbeatInterval, lease time.Duration
}

func readRuntimeOptions() (runtimeOptions, error) {
	var result runtimeOptions
	var err error
	result.runtimeInsecure, err = strconv.ParseBool(envOr("EDGE_RUNTIME_INSECURE", "0"))
	if err != nil {
		return result, fmt.Errorf("EDGE_RUNTIME_INSECURE: %w", err)
	}
	for _, item := range []struct {
		key                        string
		dst                        *time.Duration
		fallback, minimum, maximum time.Duration
	}{
		{"EDGE_STARTUP_TIMEOUT", &result.startupTimeout, 30 * time.Second, time.Second, 5 * time.Minute},
		{"EDGE_CONTROL_TIMEOUT", &result.controlTimeout, 10 * time.Second, time.Second, time.Minute},
		{"EDGE_TELEMETRY_INTERVAL", &result.telemetryInterval, 2 * time.Second, 100 * time.Millisecond, time.Minute},
		{"EDGE_HEARTBEAT_INTERVAL", &result.heartbeatInterval, 5 * time.Second, 100 * time.Millisecond, time.Minute},
		{"EDGE_LEASE", &result.lease, 15 * time.Second, time.Second, 5 * time.Minute},
	} {
		value := item.fallback
		if raw := os.Getenv(item.key); raw != "" {
			value, err = time.ParseDuration(raw)
			if err != nil {
				return result, fmt.Errorf("%s: %w", item.key, err)
			}
		}
		if value < item.minimum || value > item.maximum {
			return result, fmt.Errorf("%s must be between %s and %s", item.key, item.minimum, item.maximum)
		}
		*item.dst = value
	}
	if result.lease < 2*result.heartbeatInterval {
		return result, fmt.Errorf("EDGE_LEASE must allow at least two heartbeat intervals")
	}
	if _, err := parseFloats(os.Getenv("EDGE_WORLD_POSE"), 4); err != nil {
		return result, fmt.Errorf("EDGE_WORLD_POSE: %w", err)
	}
	if source := envOr("EDGE_TASK_SOURCE", "http"); source != "http" && source != "redis" {
		return result, fmt.Errorf("EDGE_TASK_SOURCE must be http or redis")
	}
	return result, nil
}

func durablePaths(robotID string) (journal, pin string, err error) {
	robotJournal := fmt.Sprintf("%x", sha256.Sum256([]byte(robotID)))
	journal, err = filepath.Abs(envOr("EDGE_EXECUTION_DB", filepath.Join("artifacts", "edge-worker", robotJournal[:16], "execution.db")))
	if err != nil {
		return "", "", err
	}
	pin, err = filepath.Abs(envOr("EDGE_RUNTIME_CONTRACT_FILE", journal+".runtime-contract.json"))
	if err != nil {
		return "", "", err
	}
	if journal == pin {
		return "", "", fmt.Errorf("EDGE_RUNTIME_CONTRACT_FILE must differ from EDGE_EXECUTION_DB")
	}
	return journal, pin, nil
}
