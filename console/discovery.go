package console

import (
	"net/http"

	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/discovery"
)

// Discovered robots, as the console shows them.
//
// This is the endpoint that makes "power it on and it appears" true rather than
// aspirational. Before it, joining an agent to a robot began with a person typing
// the robot's hostname into a script, and nothing on either side ever volunteered
// that the other existed.
//
// The endpoint is read-only and reports what was heard. It never probes an
// address, never connects to a robot and never pairs: finding a robot and joining
// it are separate acts, and merging them would mean a robot is joined to whatever
// answers first on a shared network.

// discoveredRobotView is one robot, as the console renders it.
type discoveredRobotView struct {
	RobotID   string `json:"robotId"`
	Hostname  string `json:"hostname"`
	Address   string `json:"address"`
	Adapter   string `json:"adapter"`
	SourceIP  string `json:"sourceIp"`
	FirstSeen string `json:"firstSeen"`
	LastSeen  string `json:"lastSeen"`
	// PairingState is what the robot says about itself. It is unverified by
	// design: an announcement is unauthenticated, and the decision about whether
	// a pairing is possible is made by the robot when the request arrives.
	PairingState string `json:"pairingState"`
	// NeedsPairing and PairingOpen are the two questions a console asks: does this
	// robot work yet, and is it ready to be joined right now.
	NeedsPairing bool `json:"needsPairing"`
	PairingOpen  bool `json:"pairingOpen"`
	// CapabilityCount is the robot's own summary of what it offers.
	CapabilityCount int `json:"capabilityCount"`
}

type discoveryView struct {
	// Listening says whether anything is looking.
	//
	// It is the difference between "there are no robots on this network" and
	// "nothing is looking for robots", and a console that showed an empty list
	// for both would send an operator to check a robot that is fine.
	Listening bool `json:"listening"`
	// Robots is what has been heard recently.
	Robots []discoveredRobotView `json:"robots"`
	// Unreadable counts datagrams on the announcement port that were not
	// announcements, and Mismatched counts robots announcing a protocol version
	// this build cannot read. The second is the actionable one: a robot is there
	// and the reason it is not listed is a version difference.
	Unreadable int `json:"unreadable"`
	Mismatched int `json:"mismatched"`
	// Hint explains an empty list, when there is something to explain.
	Hint string `json:"hint,omitempty"`
}

// discoveredRobotReporter is what the console needs from whatever is listening.
type discoveredRobotReporter interface {
	DiscoveredRobots() ([]discovery.Robot, bool)
}

func (s *Server) discoveredRobots(w http.ResponseWriter, _ *http.Request) {
	reporter, ok := s.executor.(discoveredRobotReporter)
	if !ok {
		writeJSON(w, http.StatusOK, discoveryView{
			Listening: false,
			Robots:    []discoveredRobotView{},
			Hint:      "这台 Local Agent 没有在监听机器人广播；请重启它，或改用机器人地址手动配对。",
		})
		return
	}
	robots, listening := reporter.DiscoveredRobots()
	view := discoveryView{Listening: listening, Robots: make([]discoveredRobotView, 0, len(robots))}
	for _, robot := range robots {
		view.Robots = append(view.Robots, discoveredRobotView{
			RobotID: robot.RobotID, Hostname: robot.Hostname, Address: robot.Address,
			Adapter: robot.Adapter, SourceIP: robot.SourceIP,
			FirstSeen:       robot.FirstSeen.UTC().Format("2006-01-02T15:04:05Z07:00"),
			LastSeen:        robot.LastSeen.UTC().Format("2006-01-02T15:04:05Z07:00"),
			PairingState:    robot.PairingState,
			NeedsPairing:    robot.PairingState != discovery.PairingPaired,
			PairingOpen:     robot.PairingState == discovery.PairingOpen,
			CapabilityCount: robot.CapabilityCount,
		})
	}
	switch {
	case !listening:
		view.Hint = "这台 Local Agent 没有在监听机器人广播。"
	case len(view.Robots) == 0:
		view.Hint = "还没有听到机器人广播。确认机器人和这台电脑在同一个网络，并且机器人本体服务已经启动。"
	}
	writeJSON(w, http.StatusOK, view)
}
