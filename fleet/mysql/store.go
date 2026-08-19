// Package mysql is the cloud Fleet task repository adapter. It stores each
// task as a JSON document so the distributed control plane can evolve task
// shapes without immediate schema migrations.
package mysql

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	_ "github.com/go-sql-driver/mysql"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type Store struct {
	db *sql.DB
}

func Open(dsn string) (*Store, error) {
	if dsn == "" {
		return nil, errors.New("mysql DSN is required")
	}
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(20)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(30 * time.Minute)
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, err
	}
	if _, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS robot_tasks (
			id VARCHAR(128) PRIMARY KEY,
			data JSON NOT NULL,
			updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
			INDEX idx_robot_tasks_updated (updated_at)
		) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
	`); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db: db}, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) Create(ctx context.Context, task *tasks.Task) error {
	data, err := json.Marshal(task)
	if err != nil {
		return err
	}
	_, err = s.db.ExecContext(ctx, `INSERT INTO robot_tasks (id, data, updated_at) VALUES (?, ?, ?)`, task.ID, data, task.UpdatedAt)
	return err
}

func (s *Store) Get(ctx context.Context, id string) (*tasks.Task, error) {
	var data []byte
	err := s.db.QueryRowContext(ctx, `SELECT data FROM robot_tasks WHERE id = ?`, id).Scan(&data)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, tasks.ErrTaskNotFound
	}
	if err != nil {
		return nil, err
	}
	var task tasks.Task
	if err := json.Unmarshal(data, &task); err != nil {
		return nil, err
	}
	return &task, nil
}

func (s *Store) Update(ctx context.Context, task *tasks.Task) error {
	data, err := json.Marshal(task)
	if err != nil {
		return err
	}
	result, err := s.db.ExecContext(ctx, `UPDATE robot_tasks SET data = ?, updated_at = ? WHERE id = ?`, data, task.UpdatedAt, task.ID)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected == 0 {
		return tasks.ErrTaskNotFound
	}
	return nil
}

func (s *Store) List(ctx context.Context) ([]*tasks.Task, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT data FROM robot_tasks ORDER BY updated_at, id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var list []*tasks.Task
	for rows.Next() {
		var data []byte
		if err := rows.Scan(&data); err != nil {
			return nil, err
		}
		var task tasks.Task
		if err := json.Unmarshal(data, &task); err != nil {
			return nil, err
		}
		list = append(list, &task)
	}
	return list, rows.Err()
}
