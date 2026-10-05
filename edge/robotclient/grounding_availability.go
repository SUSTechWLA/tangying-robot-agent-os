package robotclient

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

// waitGroundingCapability refreshes only runtime metadata. A capability absent
// from the advertised catalog is different from one whose sensors are briefly
// unavailable. Neither condition authorizes executing a physical command.
func (c *Client) waitGroundingCapability(ctx context.Context, initial runtime.Snapshot, skill string) (runtime.Snapshot, error) {
	waitCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	snapshot := initial
	for {
		if err := ctx.Err(); err != nil {
			return runtime.Snapshot{}, err
		}
		unavailable := snapshot.CanExecute(skill)
		if unavailable == nil {
			return snapshot, nil
		}
		if !errors.Is(unavailable, runtime.ErrCapabilityUnavailable) {
			return runtime.Snapshot{}, unavailable
		}
		timer := time.NewTimer(100 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return runtime.Snapshot{}, ctx.Err()
		case <-waitCtx.Done():
			timer.Stop()
			return runtime.Snapshot{}, unavailable
		case <-timer.C:
		}
		fresh, err := c.Info(waitCtx)
		if err != nil {
			if ctx.Err() != nil {
				return runtime.Snapshot{}, ctx.Err()
			}
			if waitCtx.Err() != nil {
				return runtime.Snapshot{}, unavailable
			}
			return runtime.Snapshot{}, fmt.Errorf("refresh grounding capabilities: %w", err)
		}
		if fresh.RobotID != initial.RobotID || fresh.Adapter != initial.Adapter || fresh.CatalogRevision != initial.CatalogRevision {
			return runtime.Snapshot{}, errors.New("grounding runtime identity or catalog changed during readiness wait")
		}
		snapshot = fresh
	}
}
