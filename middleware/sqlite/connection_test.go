package sqlite

import (
	"context"
	"database/sql"
	"path/filepath"
	"testing"
)

func TestEveryPooledConnectionWaitsForWriterAndEnforcesForeignKeys(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	first, err := store.db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	second, err := store.db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	for _, connection := range []*sql.Conn{first, second} {
		var waitMS, foreignKeys int
		if err := connection.QueryRowContext(context.Background(), "PRAGMA busy_timeout").Scan(&waitMS); err != nil {
			t.Fatal(err)
		}
		if err := connection.QueryRowContext(context.Background(), "PRAGMA foreign_keys").Scan(&foreignKeys); err != nil {
			t.Fatal(err)
		}
		if waitMS != 5000 || foreignKeys != 1 {
			t.Fatalf("pooled connection: busy_timeout=%d foreign_keys=%d", waitMS, foreignKeys)
		}
	}
}
