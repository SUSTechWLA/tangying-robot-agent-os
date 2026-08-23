package toolcatalog

import (
	"slices"
	"testing"
)

func TestRevisionIgnoresAdvertisementOrderWithoutMutatingInput(t *testing.T) {
	tools := []Tool{
		{
			Name:             "manipulation.place",
			InputParameters:  []string{"destinationId", "objectId"},
			OutputParameters: []string{"observationId", "success"},
			SideEffectClass:  PhysicalAtomic,
		},
		{Name: "observe_scene", SideEffectClass: ReadOnly},
	}
	originalInputs := slices.Clone(tools[0].InputParameters)
	originalOutputs := slices.Clone(tools[0].OutputParameters)
	reversed := []Tool{tools[1], tools[0]}

	first, err := Revision(tools)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Revision(reversed)
	if err != nil {
		t.Fatal(err)
	}
	if first != second {
		t.Fatalf("equivalent catalogs have different revisions: %q != %q", first, second)
	}
	if len(first) != 64 {
		t.Fatalf("revision length = %d, want 64", len(first))
	}
	if !slices.Equal(tools[0].InputParameters, originalInputs) ||
		!slices.Equal(tools[0].OutputParameters, originalOutputs) {
		t.Fatal("revision calculation mutated the runtime advertisement")
	}
}

func TestNewSnapshotRejectsDuplicateToolNames(t *testing.T) {
	_, err := NewSnapshot("robot-1", "mujoco", "0.1.0", []Tool{
		{Name: "observe_scene", SideEffectClass: ReadOnly},
		{Name: "observe_scene", SideEffectClass: ReadOnly},
	})
	if err == nil {
		t.Fatal("duplicate tool names were accepted")
	}
}

func TestDisplayMetadataParticipatesInCatalogRevision(t *testing.T) {
	base := Tool{Name: "manipulation.pick", SideEffectClass: PhysicalAtomic,
		DisplayName: "拿稳物品", Purpose: "安全拿起指定物品", SafeArgumentNames: []string{"targetRef"}}
	changed := base
	changed.DisplayName = "拿起物品"
	first, err := Revision([]Tool{base})
	if err != nil {
		t.Fatal(err)
	}
	second, err := Revision([]Tool{changed})
	if err != nil {
		t.Fatal(err)
	}
	if first == second {
		t.Fatal("display contract changed without changing catalog revision")
	}
}

func TestSafeArgumentNamesAreCanonicalWithoutMutatingAdvertisement(t *testing.T) {
	tool := Tool{Name: "manipulation.pick", SideEffectClass: PhysicalAtomic,
		SafeArgumentNames: []string{"targetRef", "speed", "targetRef"}}
	snapshot, err := NewSnapshot("robot-1", "mujoco", "1.0", []Tool{tool})
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(snapshot.Tools[0].SafeArgumentNames, []string{"speed", "targetRef"}) {
		t.Fatalf("canonical safe arguments = %v", snapshot.Tools[0].SafeArgumentNames)
	}
	if !slices.Equal(tool.SafeArgumentNames, []string{"targetRef", "speed", "targetRef"}) {
		t.Fatalf("canonicalization mutated input = %v", tool.SafeArgumentNames)
	}
}
