package orchestrator

import (
	"context"
	"encoding/json"

	"github.com/jackc/pgx/v5/pgxpool"
)

type PostgresStore struct {
	pool *pgxpool.Pool
}

func NewPostgresStore(ctx context.Context, databaseURL string) (*PostgresStore, error) {
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return &PostgresStore{pool: pool}, nil
}

func (s *PostgresStore) Close() { s.pool.Close() }

func (s *PostgresStore) Create(ctx context.Context, task *Task) error {
	data, err := json.Marshal(task)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx, `INSERT INTO robot_tasks (id, data, updated_at) VALUES ($1, $2, $3)`, task.ID, data, task.UpdatedAt)
	return err
}

func (s *PostgresStore) Get(ctx context.Context, id string) (*Task, error) {
	var data []byte
	if err := s.pool.QueryRow(ctx, `SELECT data FROM robot_tasks WHERE id = $1`, id).Scan(&data); err != nil {
		return nil, ErrTaskNotFound
	}
	var task Task
	if err := json.Unmarshal(data, &task); err != nil {
		return nil, err
	}
	return &task, nil
}

func (s *PostgresStore) Update(ctx context.Context, task *Task) error {
	data, err := json.Marshal(task)
	if err != nil {
		return err
	}
	result, err := s.pool.Exec(ctx, `UPDATE robot_tasks SET data = $2, updated_at = $3 WHERE id = $1`, task.ID, data, task.UpdatedAt)
	if err != nil {
		return err
	}
	if result.RowsAffected() == 0 {
		return ErrTaskNotFound
	}
	return nil
}

func (s *PostgresStore) List(ctx context.Context) ([]*Task, error) {
	rows, err := s.pool.Query(ctx, `SELECT data FROM robot_tasks ORDER BY updated_at, id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var tasks []*Task
	for rows.Next() {
		var data []byte
		if err := rows.Scan(&data); err != nil {
			return nil, err
		}
		var task Task
		if err := json.Unmarshal(data, &task); err != nil {
			return nil, err
		}
		tasks = append(tasks, &task)
	}
	return tasks, rows.Err()
}
