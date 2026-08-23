package observation

import (
	"slices"
	"testing"
	"time"
)

func TestCatalogRevisionIsStableAndDoesNotMutateSources(t *testing.T) {
	sources := []SourceDescriptor{
		{
			SourceID:          "scene",
			SourceType:        SourceRGBDCamera,
			Kind:              EntityUpsert,
			FrameID:           "camera",
			TransformRevision: "cal-7",
			MaxAge:            500 * time.Millisecond,
			FrameIDs:          []string{"camera", "world"},
		},
		{
			SourceID:          "proprioception",
			SourceType:        SourceProprioception,
			Kind:              RobotStateUpsert,
			FrameID:           "base",
			TransformRevision: "urdf-3",
			MaxAge:            100 * time.Millisecond,
			FrameIDs:          []string{"base", "world"},
		},
	}
	originalFrames := slices.Clone(sources[0].FrameIDs)

	revision, err := CatalogRevision(sources)
	if err != nil {
		t.Fatal(err)
	}
	sources[0], sources[1] = sources[1], sources[0]
	reordered, err := CatalogRevision(sources)
	if err != nil {
		t.Fatal(err)
	}
	if revision != reordered || len(revision) != 64 {
		t.Fatalf("revisions %q and %q", revision, reordered)
	}
	if !slices.Equal(sources[1].FrameIDs, originalFrames) {
		t.Fatal("catalog revision mutated source frames")
	}
}

func TestCatalogRevisionRejectsDuplicateSources(t *testing.T) {
	source := SourceDescriptor{
		SourceID: "scene", SourceType: SourceSimGroundTruth, Kind: EntityUpsert,
		FrameID: "world", TransformRevision: "scene-v1", MaxAge: time.Second,
	}
	if _, err := CatalogRevision([]SourceDescriptor{source, source}); err == nil {
		t.Fatal("duplicate source was accepted")
	}
}
