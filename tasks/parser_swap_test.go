package tasks_test

import (
	"context"
	"sync"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The parser in force is read under its own lock, and it can be replaced while
// the service is running.
//
// The model configuration is an operator setting; needing a restart to apply it
// makes it a deployment parameter pretending to be a setting.
func TestTheParserCanBeReplacedWithoutARestart(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	replacement := &countingParser{}
	service.SetParser(replacement)

	if _, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco"); err != nil {
		t.Fatalf("create with the replacement parser: %v", err)
	}
	if replacement.calls() == 0 {
		t.Fatal("the replacement parser was never used")
	}
}

// A nil parser is refused rather than stored: a service that cannot parse anything
// would accept no task and report no reason.
func TestANilParserIsRefused(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	service.SetParser(nil)
	// The original still works, which is the point: refusing did not break it.
	if _, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco"); err != nil {
		t.Fatalf("create after a refused swap: %v", err)
	}
}

// Swapping while requests are being parsed must not race. Run with -race.
func TestSwappingTheParserWhileParsingIsSafe(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	done := make(chan struct{})
	var wait sync.WaitGroup
	wait.Add(2)
	go func() {
		defer wait.Done()
		for {
			select {
			case <-done:
				return
			default:
				_, _ = service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
			}
		}
	}()
	go func() {
		defer wait.Done()
		for index := 0; index < 200; index++ {
			service.SetParser(&countingParser{})
			service.SetParser(intent.NewDeterministicParser())
		}
		close(done)
	}()
	wait.Wait()
}

type countingParser struct {
	mu    sync.Mutex
	count int
}

func (p *countingParser) Parse(string) (manipulation.Intent, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.count++
	return manipulation.Intent{}, nil
}

func (p *countingParser) calls() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.count
}
