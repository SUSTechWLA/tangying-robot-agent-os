package mysql

import (
	"database/sql"
	"fmt"
	"os"
	"testing"
	"time"

	driver "github.com/go-sql-driver/mysql"
)

// MYSQL_TEST_DSN must point to an isolated MySQL test server with CREATE/DROP
// DATABASE permission. Each case owns a unique database; user tables are never
// changed. Run against the same MySQL major version as deploy/cloud.
func TestMySQLRevisionMigrationFreshAndLegacy(t *testing.T) {
	dsn := os.Getenv("MYSQL_TEST_DSN")
	if dsn == "" {
		t.Skip("set MYSQL_TEST_DSN to exercise the real MySQL migration")
	}
	config, err := driver.ParseDSN(dsn)
	if err != nil {
		t.Fatal(err)
	}
	config.DBName = ""
	admin, err := sql.Open("mysql", config.FormatDSN())
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	for _, legacy := range []bool{false, true} {
		t.Run(fmt.Sprintf("legacy=%t", legacy), func(t *testing.T) {
			name := fmt.Sprintf("robot_migration_test_%d", time.Now().UnixNano())
			if _, err := admin.Exec("CREATE DATABASE " + name); err != nil {
				t.Fatal(err)
			}
			defer func() {
				if _, err := admin.Exec("DROP DATABASE " + name); err != nil {
					t.Error(err)
				}
			}()
			cfg := *config
			cfg.DBName = name
			cfg.ParseTime = true
			db, err := sql.Open("mysql", cfg.FormatDSN())
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			if legacy {
				if _, err := db.Exec(`CREATE TABLE robot_tasks (
					id VARCHAR(128) PRIMARY KEY, data JSON NOT NULL,
					updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
				) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`); err != nil {
					t.Fatal(err)
				}
				if _, err := db.Exec(`INSERT INTO robot_tasks (id, data) VALUES ('preserved', '{"request":"keep me"}')`); err != nil {
					t.Fatal(err)
				}
			}
			for attempt := 0; attempt < 2; attempt++ {
				store, err := Open(cfg.FormatDSN())
				if err != nil {
					t.Fatalf("startup %d: %v", attempt+1, err)
				}
				store.Close()
			}
			if legacy {
				var version uint64
				var request string
				if err := db.QueryRow(`SELECT aggregate_version, JSON_UNQUOTE(data->'$.request') FROM robot_tasks WHERE id='preserved'`).Scan(&version, &request); err != nil {
					t.Fatal(err)
				}
				if version != 1 || request != "keep me" {
					t.Fatalf("migration changed task: version=%d request=%q", version, request)
				}
			}
			for _, table := range []string{"task_revisions", "task_revision_events"} {
				var count int
				if err := db.QueryRow("SELECT COUNT(*) FROM " + table).Scan(&count); err != nil {
					t.Fatalf("missing usable revision table %s: %v", table, err)
				}
			}
		})
	}
}
