package actionloop

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"golang.org/x/sys/unix"
)

// ErrModelUsageLog means explicitly configured accounting could not be
// persisted. Another model response cannot repair it; callers must retain the
// current attempt and stop, rather than treating it as invalid model output.
var ErrModelUsageLog = errors.New("model usage accounting failed")

// usageEvent is intentionally independent of Projection. It contains accounting
// and scope identifiers, never prompt text, raw responses, API keys or URLs.
type usageEvent struct {
	SchemaVersion string                 `json:"schema_version"`
	Event         string                 `json:"event"`
	Call          agentcontext.ModelCall `json:"call"`
	Scope         agentcontext.Scope     `json:"scope"`
}

// appendModelUsage uses a private regular file and an inter-process lock. A
// start without a result means accounting is incomplete, not zero-cost. A torn
// final line is preserved and blocks further appends until operator recovery.
// This ledger has no rotation policy: deployments must retain/rotate it as an
// audit artifact, and monitor disk capacity independently of robot execution.
func appendModelUsage(path, event string, call agentcontext.ModelCall, scope agentcontext.Scope) error {
	if !filepath.IsAbs(path) {
		return errors.New("model usage path must be absolute")
	}
	parent := filepath.Dir(path)
	parentInfo, err := os.Lstat(parent)
	if err != nil {
		return err
	}
	if !parentInfo.IsDir() || parentInfo.Mode().Perm()&0o022 != 0 {
		return errors.New("model usage parent must be a directory not writable by group or others")
	}
	var parentStat unix.Stat_t
	if err := unix.Stat(parent, &parentStat); err != nil {
		return err
	}
	if int(parentStat.Uid) != os.Geteuid() {
		return errors.New("model usage parent must belong to the current user")
	}
	body, err := json.Marshal(usageEvent{SchemaVersion: "agent.model-usage.v1", Event: event, Call: call, Scope: scope})
	if err != nil {
		return err
	}
	if len(body) > 64<<10 {
		return errors.New("model usage event exceeds 64 KiB")
	}
	body = append(body, '\n')
	flags := unix.O_RDWR | unix.O_APPEND | unix.O_NOFOLLOW | unix.O_CLOEXEC
	fd, err := unix.Open(path, flags|unix.O_CREAT|unix.O_EXCL, 0o600)
	if errors.Is(err, unix.EEXIST) {
		fd, err = unix.Open(path, flags, 0)
	}
	if err != nil {
		return err
	}
	file := os.NewFile(uintptr(fd), path)
	defer file.Close()
	var stat unix.Stat_t
	if err := unix.Fstat(fd, &stat); err != nil {
		return err
	}
	if stat.Mode&unix.S_IFMT != unix.S_IFREG || stat.Nlink != 1 || int(stat.Uid) != os.Geteuid() || stat.Mode&0o777 != 0o600 {
		return errors.New("model usage file must be an owned regular file with mode 0600 and no hard links")
	}
	// Bound contention with another process. Never hang the control loop waiting
	// for a log owner; configured accounting must succeed before a new HTTP call.
	lockCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	for {
		err = unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB)
		if err == nil {
			break
		}
		if !errors.Is(err, unix.EWOULDBLOCK) && !errors.Is(err, unix.EAGAIN) && !errors.Is(err, unix.EINTR) {
			return err
		}
		select {
		case <-lockCtx.Done():
			return errors.New("model usage file lock timed out")
		case <-time.After(5 * time.Millisecond):
		}
	}
	defer unix.Flock(fd, unix.LOCK_UN)
	info, err := file.Stat()
	if err != nil {
		return err
	}
	if info.Size() > 0 {
		last := make([]byte, 1)
		if _, err := file.ReadAt(last, info.Size()-1); err != nil {
			return err
		}
		if last[0] != '\n' {
			return errors.New("model usage file has an incomplete trailing line; preserve and recover it before appending")
		}
	}
	if n, err := file.Write(body); err != nil {
		return err
	} else if n != len(body) {
		return io.ErrShortWrite
	}
	if err := file.Sync(); err != nil {
		return fmt.Errorf("sync model usage file: %w", err)
	}
	// Another process may have created this path just before our open. Always
	// sync the directory so our successful start never relies on that creator
	// surviving long enough to make the new directory entry durable.
	directory, err := os.Open(parent)
	if err != nil {
		return err
	}
	defer directory.Close()
	if err := directory.Sync(); err != nil {
		return fmt.Errorf("sync model usage directory: %w", err)
	}
	return nil
}
