package controllease

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDataDirectoryOwnerAndAlias(t *testing.T) {
	dir := t.TempDir()
	first, err := AcquireDataDirectory(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	alias := filepath.Join(t.TempDir(), "alias")
	if err := os.Symlink(dir, alias); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{dir, alias} {
		if other, err := AcquireDataDirectory(path); err == nil {
			other.Close()
			t.Fatal("second journal owner accepted")
		}
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	replacement, err := AcquireDataDirectory(alias)
	if err != nil {
		t.Fatal(err)
	}
	replacement.Close()
}

func TestDataDirectoryRejectsSymlinkLock(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(t.TempDir(), "target")
	if err := os.WriteFile(target, []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, filepath.Join(dir, ".agent-owner.lock")); err != nil {
		t.Fatal(err)
	}
	if lock, err := AcquireDataDirectory(dir); err == nil {
		lock.Close()
		t.Fatal("symlink lock accepted")
	}
	if data, _ := os.ReadFile(target); string(data) != "keep" {
		t.Fatal("target changed")
	}
}
