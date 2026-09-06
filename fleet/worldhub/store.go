package worldhub

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
	"golang.org/x/sys/unix"
)

var ErrSnapshotNotFound = errors.New("world checkpoint does not exist")

// SnapshotStore.Save must durably commit a complete checkpoint before success.
// An error may represent an uncertain commit; the Hub requires recovery after it.
type SnapshotStore interface {
	Load(context.Context) (worldmodel.Checkpoint, error)
	Save(context.Context, worldmodel.Checkpoint) error
}

// FileStore is a local, single-primary checkpoint store. Its advisory lock only
// excludes processes using this checkpoint on the same filesystem; it does not
// provide distributed leadership or fencing. The directory must already exist
// on durable storage and must support atomic rename and file/directory fsync.
type FileStore struct {
	mu   sync.Mutex
	path string
	lock *os.File
}

type checkpointFile struct {
	SchemaVersion string          `json:"schemaVersion"`
	SHA256        string          `json:"sha256"`
	Checkpoint    json.RawMessage `json:"checkpoint"`
}

func OpenFileStore(path string) (*FileStore, error) {
	if strings.TrimSpace(path) == "" {
		return nil, errors.New("world checkpoint path is required")
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	directory, err := filepath.EvalSymlinks(filepath.Dir(absolute))
	if err != nil {
		return nil, fmt.Errorf("world checkpoint directory must exist: %w", err)
	}
	absolute = filepath.Join(directory, filepath.Base(absolute))
	if info, err := os.Lstat(absolute); err == nil && !info.Mode().IsRegular() {
		return nil, errors.New("world checkpoint must be a regular file")
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	lock, err := os.OpenFile(absolute+".lock", os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, fmt.Errorf("open world checkpoint lock: %w", err)
	}
	if err := unix.Flock(int(lock.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		lock.Close()
		return nil, fmt.Errorf("world checkpoint is already locked: %w", err)
	}
	return &FileStore{path: absolute, lock: lock}, nil
}

func (s *FileStore) Load(ctx context.Context) (worldmodel.Checkpoint, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ready(ctx); err != nil {
		return worldmodel.Checkpoint{}, err
	}
	data, err := os.ReadFile(s.path)
	if errors.Is(err, os.ErrNotExist) {
		return worldmodel.Checkpoint{}, ErrSnapshotNotFound
	}
	if err != nil {
		return worldmodel.Checkpoint{}, err
	}
	var stored checkpointFile
	if err := decodeCheckpointJSON(data, &stored); err != nil {
		return worldmodel.Checkpoint{}, fmt.Errorf("decode world checkpoint: %w", err)
	}
	digest := sha256.Sum256(stored.Checkpoint)
	if stored.SchemaVersion != "world.checkpoint.file.v1" || stored.SHA256 != hex.EncodeToString(digest[:]) {
		return worldmodel.Checkpoint{}, errors.New("world checkpoint schema or checksum mismatch")
	}
	var checkpoint worldmodel.Checkpoint
	if err := decodeCheckpointJSON(stored.Checkpoint, &checkpoint); err != nil {
		return worldmodel.Checkpoint{}, fmt.Errorf("decode projector checkpoint: %w", err)
	}
	return checkpoint, nil
}

func (s *FileStore) Save(ctx context.Context, checkpoint worldmodel.Checkpoint) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ready(ctx); err != nil {
		return err
	}
	payload, err := json.Marshal(checkpoint)
	if err != nil {
		return fmt.Errorf("encode world checkpoint: %w", err)
	}
	digest := sha256.Sum256(payload)
	data, err := json.Marshal(checkpointFile{SchemaVersion: "world.checkpoint.file.v1", SHA256: hex.EncodeToString(digest[:]), Checkpoint: payload})
	if err != nil {
		return err
	}
	directory, err := os.Open(filepath.Dir(s.path))
	if err != nil {
		return err
	}
	defer directory.Close()
	temporary, err := os.CreateTemp(filepath.Dir(s.path), "."+filepath.Base(s.path)+".tmp-*")
	if err != nil {
		return err
	}
	defer func() { _ = temporary.Close(); _ = os.Remove(temporary.Name()) }()
	if _, err := temporary.Write(data); err != nil {
		return err
	}
	if err := temporary.Sync(); err != nil {
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := os.Rename(temporary.Name(), s.path); err != nil {
		return err
	}
	// A successful rename alone does not guarantee recovery after power loss.
	return directory.Sync()
}

func (s *FileStore) ready(ctx context.Context) error {
	if s.lock == nil {
		return errors.New("world checkpoint store is closed")
	}
	return ctx.Err()
}

func (s *FileStore) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.lock == nil {
		return nil
	}
	err := s.lock.Close()
	s.lock = nil
	return err
}

func decodeCheckpointJSON(data []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(new(any)); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("trailing JSON value")
		}
		return err
	}
	return nil
}
