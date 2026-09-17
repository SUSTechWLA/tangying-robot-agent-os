package discovery

import (
	"context"
	"errors"
	"log"
	"net"
	"sort"
	"sync"
	"time"
)

// DefaultRetention is how long a robot stays in the list after its last
// announcement.
//
// A robot that is switched off, unplugged or carried out of range must disappear
// on its own. Three announcement intervals is long enough that one lost datagram
// does not make a robot flicker out of the list, and short enough that a list an
// operator is reading is about the present rather than about the last hour.
const DefaultRetention = 3 * DefaultInterval

// DefaultInterval is how often a robot announces, and therefore the cadence the
// retention window is expressed in.
const DefaultInterval = 5 * time.Second

// Robot is one discovered robot as the console sees it.
type Robot struct {
	Announcement
	// SourceIP is where the datagram actually came from.
	//
	// It is recorded separately from the announced address on purpose. The
	// announced address is what the robot believes, and it is what a connection
	// should use; the source is what the network says. When they disagree — a
	// robot behind a NAT, a stale address after a DHCP change, a spoofed packet —
	// an operator needs to see both, and a console that showed only one of them
	// would make the disagreement invisible.
	SourceIP string `json:"sourceIp"`
	// FirstSeen and LastSeen bound how long this robot has been announcing.
	FirstSeen time.Time `json:"firstSeen"`
	LastSeen  time.Time `json:"lastSeen"`
	// PairingCodeRequired says whether a pairing request for this robot must
	// carry the code printed on it. It is derived from the announced pairing
	// state, and it exists so a console can ask for the code before the user
	// tries and fails.
	PairingCodeRequired bool `json:"pairingCodeRequired"`
}

// Age reports how long ago this robot last announced.
func (r Robot) Age(now time.Time) time.Duration {
	return now.Sub(r.LastSeen)
}

// Listener collects announcements from robots on the local network.
//
// It is a passive listener: it never probes an address, never connects to a
// robot, and never changes anything. Its whole output is a list, which is what
// keeps "the agent can see a robot" from being a statement about the robot's
// state rather than about the agent's knowledge.
type Listener struct {
	// Port is the UDP port to listen on. Zero means AnnouncementPort.
	Port int
	// Retention is how long a robot is remembered. Zero means DefaultRetention.
	Retention time.Duration
	// Now is the clock. Tests set it; production leaves it nil.
	Now func() time.Time
	// OnChange, when set, is called after the list changes.
	//
	// It is a nudge for a console that pushes rather than polls. It runs on the
	// receiving goroutine, so it must return promptly and must not block on
	// anything that talks to a robot.
	OnChange func([]Robot)

	mu      sync.Mutex
	robots  map[string]Robot
	conn    *net.UDPConn
	stopped bool
	// dropped counts datagrams on the announcement port that were not
	// announcements at all.
	dropped int
	// mismatched counts announcements that are ours but from a protocol version
	// this build does not understand.
	//
	// It is a separate counter from dropped because the two mean opposite
	// things to an operator. Foreign traffic is noise — some other product
	// broadcasts on its own port and this one is shared. A version mismatch is a
	// robot: it is on the network, it is trying to be found, and the reason it
	// cannot be is that the two ends disagree about the format. Folding them
	// together would turn "your robot is running newer firmware" into "something
	// is spraying packets".
	mismatched int
}

// NewListener creates a listener. It does not bind until Listen is called.
func NewListener() *Listener {
	return &Listener{robots: map[string]Robot{}}
}

// Listen binds the announcement port and collects until the context is done.
//
// It returns when the context is cancelled or Stop is called. A bind failure is
// returned rather than retried in a loop: a port that cannot be bound is a
// deployment fact an operator has to know about, and a listener that quietly
// retried forever would present itself as "no robots on this network".
func (l *Listener) Listen(ctx context.Context) error {
	port := l.Port
	if port <= 0 {
		port = AnnouncementPort
	}
	connection, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4zero, Port: port})
	if err != nil {
		return err
	}
	l.mu.Lock()
	if l.stopped {
		l.mu.Unlock()
		_ = connection.Close()
		return nil
	}
	l.conn = connection
	l.mu.Unlock()

	// A sweep goroutine ages robots out. It exists because retention is about
	// elapsed time, not about traffic: on a network where every robot has gone
	// quiet, nothing arrives to trigger a cleanup, and without this the list
	// would keep showing robots that are no longer there.
	sweepDone := make(chan struct{})
	go func() {
		defer close(sweepDone)
		ticker := time.NewTicker(l.retention() / 3)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if l.expire() {
					l.notify()
				}
			}
		}
	}()

	buffer := make([]byte, MaxAnnouncementSize)
	for {
		count, source, err := connection.ReadFromUDP(buffer)
		if err != nil {
			<-sweepDone
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		if l.absorb(buffer[:count], source) {
			l.notify()
		}
	}
}

// Stop releases the socket. It is safe to call more than once.
func (l *Listener) Stop() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.stopped = true
	if l.conn == nil {
		return nil
	}
	err := l.conn.Close()
	l.conn = nil
	return err
}

// Robots returns the currently visible robots, most recently seen first.
func (l *Listener) Robots() []Robot {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.sortedLocked(l.now())
}

// Dropped reports how many datagrams on this port were not announcements.
//
// It is reported because a non-zero value turns "no robots found" into a
// diagnosis: something is talking on this port and it is not a robot.
func (l *Listener) Dropped() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.dropped
}

// Mismatched reports how many robots announced a protocol version this build
// cannot read.
//
// A non-zero value here is the most actionable number in this package: it means
// a robot is present, reachable and announcing, and the reason it is not in the
// list is a version difference between it and this agent.
func (l *Listener) Mismatched() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.mismatched
}

// absorb records one datagram. It returns whether the visible list changed.
func (l *Listener) absorb(data []byte, source *net.UDPAddr) bool {
	announcement, err := DecodeAnnouncement(data)
	if err != nil {
		// Counted, never logged per packet: one foreign broadcaster on the port
		// would otherwise fill the log with the same line forever.
		l.mu.Lock()
		switch {
		case errors.Is(err, ErrUnsupportedVersion):
			l.mismatched++
		case errors.Is(err, ErrNotAnnouncement):
			l.dropped++
		}
		l.mu.Unlock()
		return false
	}
	if announcement.SentAt.IsZero() {
		announcement.SentAt = l.now()
	}
	robot := Robot{
		Announcement:        announcement,
		FirstSeen:           l.now(),
		LastSeen:            l.now(),
		PairingCodeRequired: announcement.PairingState == PairingOpen,
	}
	if source != nil {
		robot.SourceIP = source.IP.String()
	}

	l.mu.Lock()
	defer l.mu.Unlock()
	if previous, seen := l.robots[announcement.RobotID]; seen {
		// The first sighting is kept: how long a robot has been visible is a
		// fact about this listener's knowledge, and resetting it on every
		// announcement would always report a robot as just appeared.
		robot.FirstSeen = previous.FirstSeen
	}
	l.robots[announcement.RobotID] = robot
	return true
}

// expire drops robots that have not announced inside the retention window. It
// returns whether anything was removed.
func (l *Listener) expire() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	removed := false
	for id, robot := range l.robots {
		if now.Sub(robot.LastSeen) > l.retention() {
			delete(l.robots, id)
			removed = true
		}
	}
	return removed
}

func (l *Listener) sortedLocked(now time.Time) []Robot {
	robots := make([]Robot, 0, len(l.robots))
	for _, robot := range l.robots {
		robots = append(robots, robot)
	}
	sort.SliceStable(robots, func(i, j int) bool {
		// Robots accepting a pairing request come first: the list exists to
		// answer "which one do I connect to", and a robot that is ready to be
		// connected to is the answer more often than the others.
		leftOpen := robots[i].PairingState == PairingOpen
		rightOpen := robots[j].PairingState == PairingOpen
		if leftOpen != rightOpen {
			return leftOpen
		}
		if !robots[i].LastSeen.Equal(robots[j].LastSeen) {
			return robots[i].LastSeen.After(robots[j].LastSeen)
		}
		return robots[i].RobotID < robots[j].RobotID
	})
	return robots
}

func (l *Listener) notify() {
	if l.OnChange == nil {
		return
	}
	robots := l.Robots()
	l.OnChange(robots)
}

func (l *Listener) retention() time.Duration {
	if l.Retention > 0 {
		return l.Retention
	}
	return DefaultRetention
}

func (l *Listener) now() time.Time {
	if l.Now != nil {
		return l.Now().UTC()
	}
	return time.Now().UTC()
}

// StartInBackground binds the listener and collects until the process ends.
//
// A discovery failure is logged and returned, never fatal. An agent that cannot
// listen for robots is exactly as useful as one that has not found any yet, and
// refusing to start would replace a degraded experience with no experience —
// the same rule the agent runtime follows about its observers.
func StartInBackground(ctx context.Context) *Listener {
	listener := NewListener()
	go func() {
		if err := listener.Listen(ctx); err != nil && ctx.Err() == nil {
			log.Printf("robot discovery not started: %v", err)
		}
	}()
	return listener
}
