package exchange

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"sort"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
)

type CoverageContribution struct {
	MessageID    string     `json:"messageId"`
	Sender       x.Identity `json:"sender"`
	Stream       string     `json:"stream"`
	Sequence     uint64     `json:"sequence,string"`
	FeedRevision uint64     `json:"feedRevision,string"`
	ObservedAtMS int64      `json:"observedAtMs"`
	TargetIDs    []string   `json:"targetIds"`
}
type RejectedAdvisory struct {
	MessageID string `json:"messageId"`
	Reason    string `json:"reason"`
}
type CoverageView struct {
	SchemaVersion            string                 `json:"schemaVersion"`
	MissionID                string                 `json:"missionId"`
	MissionRevision          string                 `json:"missionRevision"`
	GeometryID               string                 `json:"geometryId"`
	Basis                    x.Basis                `json:"basis"`
	Cursor                   uint64                 `json:"cursor,string"`
	PrunedThrough            uint64                 `json:"prunedThrough,string"`
	CompleteRead             bool                   `json:"completeRead"`
	AdvisoryOnly             bool                   `json:"advisoryOnly"`
	GrantsExecution          bool                   `json:"grantsExecution"`
	PhysicalCompletion       bool                   `json:"physicalCompletion"`
	ObservedAtMS             int64                  `json:"evaluatedAtMs"`
	ProposedCoveredTargetIDs []string               `json:"proposedCoveredTargetIds"`
	RemainingTargetIDs       []string               `json:"remainingTargetIds"`
	Contributions            []CoverageContribution `json:"contributions"`
	Rejected                 []RejectedAdvisory     `json:"rejected"`
}

func (s *Service) Feed(ctx context.Context, after uint64, limit int) (x.Feed, error) {
	journal, ok := s.journal.(x.FeedJournal)
	if !ok {
		return x.Feed{}, errors.New("exchange journal has no durable feed reader")
	}
	return journal.Feed(ctx, after, limit)
}

// CoverageView joins current retained peer claims with an explicit local task
// scope. It never merges local map truth, marks a task complete or owns motion.
// A bounded/truncated read produces NO proposal rather than a misleading subset.
func (s *Service) CoverageView(ctx context.Context, q x.CoverageQuery) (CoverageView, error) {
	if err := q.Validate(); err != nil {
		return CoverageView{}, err
	}
	journal, ok := s.journal.(x.FeedJournal)
	if !ok {
		return CoverageView{}, errors.New("exchange journal has no snapshot reader")
	}
	var feed x.Feed
	var err error
	currentProjection := false
	if latest, ok := s.journal.(x.LatestSnapshotJournal); ok {
		currentProjection = true
		peers := make([]x.Identity, 0, len(s.peers))
		for _, peer := range s.peers {
			peers = append(peers, peer)
		}
		feed, err = latest.LatestSnapshot(ctx, s.identity, peers, q.Limit)
	} else {
		// Legacy adapters remain conservative: a truncated history cannot be
		// mistaken for a complete current view, even if it contains few streams.
		feed, err = journal.Snapshot(ctx, q.Limit)
	}
	if err != nil {
		return CoverageView{}, err
	}
	now := s.now().UnixMilli()
	// A legacy history reader cannot establish whether GC removed the newest
	// body of a stream while leaving its older body. It needs the optional
	// durable-cursor projection before it can safely resume after any pruning.
	complete := !feed.More && (currentProjection || feed.PrunedThrough == 0)
	view := CoverageView{SchemaVersion: "agent.coverage.view.v1", MissionID: q.MissionID, MissionRevision: q.MissionRevision, GeometryID: q.GeometryID, Basis: q.Basis, Cursor: feed.Head, PrunedThrough: feed.PrunedThrough, CompleteRead: complete, AdvisoryOnly: true, ObservedAtMS: now, ProposedCoveredTargetIDs: []string{}, RemainingTargetIDs: append([]string{}, q.TargetIDs...), Contributions: []CoverageContribution{}, Rejected: []RejectedAdvisory{}}
	if !complete {
		return view, nil
	}
	latest := map[string]uint64{}
	for _, item := range feed.Items {
		e := item.Envelope
		latest[e.Sender.AgentID+"\x00"+e.Stream] = e.Sequence
	}
	scope := map[string]bool{}
	covered := map[string]bool{}
	for _, id := range q.TargetIDs {
		scope[id] = true
	}
	for _, item := range feed.Items {
		e := item.Envelope
		reject := func(reason string) { view.Rejected = append(view.Rejected, RejectedAdvisory{e.MessageID, reason}) }
		peer, ok := s.peers[e.Sender.AgentID]
		if !ok || peer != e.Sender || e.Recipient != s.identity {
			reject("identity_no_longer_configured")
			continue
		}
		if err := e.Validate(); err != nil {
			reject("invalid_envelope")
			continue
		}
		if latest[e.Sender.AgentID+"\x00"+e.Stream] != e.Sequence {
			reject("superseded")
			continue
		}
		if err := e.Fresh(now); err != nil {
			reject("envelope_not_fresh")
			continue
		}
		if e.Kind != "observation" || e.Basis == nil || *e.Basis != q.Basis {
			reject("spatial_basis_mismatch")
			continue
		}
		var report x.CoverageReport
		decoder := json.NewDecoder(bytes.NewReader(e.Payload))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&report); err != nil || report.SchemaVersion != x.CoverageSchema {
			reject("unsupported_payload")
			continue
		}
		if report.MissionID != q.MissionID || report.MissionRevision != q.MissionRevision || report.GeometryID != q.GeometryID {
			reject("task_scope_mismatch")
			continue
		}
		if report.ObservedAtMS <= 0 || report.ObservedAtMS > e.CreatedAtMS+x.FutureSkewMS || report.ObservedAtMS > now+x.FutureSkewMS || now-report.ObservedAtMS > q.MaxAgeMS {
			reject("observation_not_fresh")
			continue
		}
		if err := x.ValidateTargetIDs(report.ObservedTargetIDs); err != nil {
			reject("invalid_target_ids")
			continue
		}
		valid := true
		for _, id := range report.ObservedTargetIDs {
			if !scope[id] {
				valid = false
				break
			}
		}
		if !valid {
			reject("target_outside_scope")
			continue
		}
		for _, id := range report.ObservedTargetIDs {
			covered[id] = true
		}
		view.Contributions = append(view.Contributions, CoverageContribution{e.MessageID, e.Sender, e.Stream, e.Sequence, item.Revision, report.ObservedAtMS, report.ObservedTargetIDs})
	}
	view.RemainingTargetIDs = []string{}
	for _, id := range q.TargetIDs {
		if covered[id] {
			view.ProposedCoveredTargetIDs = append(view.ProposedCoveredTargetIDs, id)
		} else {
			view.RemainingTargetIDs = append(view.RemainingTargetIDs, id)
		}
	}
	sort.Strings(view.ProposedCoveredTargetIDs)
	sort.Strings(view.RemainingTargetIDs)
	return view, nil
}
