// Package controllease prevents two agent processes on the same host from
// simultaneously acting as the task authority for one Robot Runtime.
package controllease

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/unix"
)

type Lock struct{ file *os.File }

// Acquire holds an advisory host-local lock for one Runtime endpoint until Close
// or process exit. Distinct simulators may advertise the same RobotID while
// listening on different ports, so RobotID alone is not a safe lock key. The
// lock file is deliberately left in place: unlinking it permits a second inode
// to be locked while the first process still owns the original inode.
func Acquire(robotID, runtimeAddress string) (*Lock, error) {
	if strings.TrimSpace(robotID) == "" || strings.TrimSpace(runtimeAddress) == "" {
		return nil, errors.New("robot ID and Runtime address are required for control lock")
	}
	directory := os.Getenv("ROBOT_CONTROL_LOCK_DIR")
	if directory == "" {
		cache, err := os.UserCacheDir()
		if err != nil {
			return nil, fmt.Errorf("find control lock directory: %w", err)
		}
		directory = filepath.Join(cache, "tangying-robot-agent-os", "locks")
		if err := os.MkdirAll(directory, 0700); err != nil {
			return nil, fmt.Errorf("create control lock directory: %w", err)
		}
	}
	if !filepath.IsAbs(directory) {
		return nil, errors.New("ROBOT_CONTROL_LOCK_DIR must be absolute")
	}
	info, err := os.Stat(directory)
	if err != nil {
		return nil, fmt.Errorf("control lock directory is unavailable: %w", err)
	}
	if !info.IsDir() {
		return nil, errors.New("control lock path is not a directory")
	}
	digest := sha256.Sum256([]byte(strings.TrimSpace(runtimeAddress)))
	path := filepath.Join(directory, "tangying-control-"+hex.EncodeToString(digest[:16])+".lock")
	fd, err := unix.Open(path, unix.O_CREAT|unix.O_RDWR|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, fmt.Errorf("open control lock for %s: %w", robotID, err)
	}
	file := os.NewFile(uintptr(fd), path)
	var stat unix.Stat_t
	if err := unix.Fstat(fd, &stat); err != nil || stat.Mode&unix.S_IFMT != unix.S_IFREG || stat.Uid != uint32(os.Getuid()) {
		file.Close()
		return nil, fmt.Errorf("control lock for %s is not a regular file owned by this user", robotID)
	}
	if err := unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB); err != nil {
		file.Close()
		return nil, fmt.Errorf("robot %s already has a controller on this host: %w", robotID, err)
	}
	return &Lock{file: file}, nil
}

func (l *Lock) Close() error {
	if l == nil || l.file == nil {
		return nil
	}
	err := l.file.Close()
	l.file = nil
	return err
}
