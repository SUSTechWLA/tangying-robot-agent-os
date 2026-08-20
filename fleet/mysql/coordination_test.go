package mysql

import (
	"strings"
	"testing"
)

func TestCoordinationSchemaContainsDurableFleetTables(t *testing.T) {
	for _, table := range []string{"fleet_domain_events", "fleet_graph_states", "fleet_checkpoints", "fleet_outbox"} {
		if !strings.Contains(coordinationSchema, table) {
			t.Fatalf("coordination schema missing %s", table)
		}
	}
}
