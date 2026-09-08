package console

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// NavigationMap is an allowlist: private ROS bridge settings never reach UI.
type NavigationMap struct {
	RobotID              string    `json:"robotId"`
	FrameID              string    `json:"frameId"`
	Ready                bool      `json:"ready"`
	Mode                 string    `json:"mode"`
	MapRevision          string    `json:"mapRevision"`
	Width                int       `json:"width"`
	Height               int       `json:"height"`
	Resolution           float64   `json:"resolution"`
	Cells                []int8    `json:"cells,omitempty"`
	Origin               []float64 `json:"origin,omitempty"`
	MapPose              []float64 `json:"mapPose,omitempty"`
	PoseSource           string    `json:"poseSource"`
	PoseObservedAtUnixMS int64     `json:"poseObservedAtUnixMs"`
	ObservedAtUnixMS     int64     `json:"observedAtUnixMs"`
	LocalizationState    string    `json:"localizationState"`
	GridUnavailable      string    `json:"gridUnavailable,omitempty"`
}

type NavigationReader struct {
	endpoint, token string
	client          *http.Client
}

func NewNavigationReader(endpoint, token string) (*NavigationReader, error) {
	if endpoint == "" {
		return nil, nil
	}
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || (parsed.Path != "" && parsed.Path != "/") || strings.TrimSpace(token) == "" {
		return nil, errors.New("navigation needs an HTTP(S) origin and a private token")
	}
	return &NavigationReader{strings.TrimRight(endpoint, "/"), token, &http.Client{
		Timeout: time.Second * 2, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}}, nil
}

func WithNavigation(reader *NavigationReader) Option {
	return func(s *Server) { s.navigation = reader }
}

func (n *NavigationReader) read(ctx context.Context) (NavigationMap, error) {
	var result NavigationMap
	req, err := http.NewRequestWithContext(ctx, "GET", n.endpoint+"/v1/navigation/map?includeGrid=1", nil)
	if err != nil {
		return result, err
	}
	req.Header.Set("Authorization", "Bearer "+n.token)
	reply, err := n.client.Do(req)
	if err != nil {
		return result, err
	}
	defer reply.Body.Close()
	if reply.StatusCode != http.StatusOK {
		return result, errors.New("navigation bridge is unavailable")
	}
	body, err := io.ReadAll(io.LimitReader(reply.Body, 2*1024*1024+1))
	if err != nil {
		return result, err
	}
	if len(body) > 2*1024*1024 {
		return result, errors.New("navigation map exceeds size limit")
	}
	// encoding/json maps null array elements to numeric zero. A missing cell
	// must never become observed free space, nor an absent pose coordinate zero.
	var fields map[string]json.RawMessage
	if err = json.Unmarshal(body, &fields); err != nil {
		return result, err
	}
	for _, name := range []string{"cells", "origin", "mapPose"} {
		value, present := fields[name]
		if !present {
			continue
		}
		var entries []json.RawMessage
		if err = json.Unmarshal(value, &entries); err != nil {
			return result, err
		}
		for _, entry := range entries {
			if bytes.Equal(bytes.TrimSpace(entry), []byte("null")) {
				return result, errors.New("navigation geometry contains a missing numeric value")
			}
		}
	}
	if err = json.Unmarshal(body, &result); err != nil {
		return result, err
	}
	if len(result.Cells) > 0 {
		if result.Width < 1 || result.Height < 1 || result.Width > 4096 || result.Height > 4096 || result.Width*result.Height != len(result.Cells) || len(result.Cells) > 262144 || !finitePositive(result.Resolution) || !navigationPose(result.Origin) || math.Abs(result.Origin[4]) > 1e-6 || math.Abs(result.Origin[5]) > 1e-6 || result.FrameID != "map" || result.MapRevision == "" {
			return result, errors.New("navigation occupancy grid is invalid")
		}
		for _, cell := range result.Cells {
			if cell < -1 || cell > 100 {
				return result, errors.New("navigation occupancy cell is invalid")
			}
		}
	}
	age := time.Now().UnixMilli() - result.PoseObservedAtUnixMS
	if result.FrameID != "map" || result.MapRevision == "" || !navigationPose(result.MapPose) || result.PoseSource != "rtabmap_tf" || age < 0 || age > 1000 {
		result.Ready = false
		result.MapPose = nil
	}
	return result, nil
}

func finitePositive(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) && v > 0 }
func navigationPose(p []float64) bool {
	if len(p) != 7 {
		return false
	}
	norm := 0.0
	for i, v := range p {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return false
		}
		if i >= 3 {
			norm += v * v
		}
	}
	return math.Abs(norm-1) < .001
}

func (s *Server) navigationMap(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if s.navigation == nil {
		writeError(w, 503, "NAVIGATION_UNAVAILABLE", "navigation is not configured")
		return
	}
	snapshot, err := s.navigation.read(r.Context())
	if err != nil {
		writeError(w, 502, "NAVIGATION_MAP_UNAVAILABLE", "navigation map is unavailable or invalid")
		return
	}
	if s.runtime != nil {
		runtime, err := s.runtime.Info(r.Context())
		if err != nil || runtime.RobotID != snapshot.RobotID {
			writeError(w, 502, "NAVIGATION_ROBOT_MISMATCH", "map does not match the connected robot")
			return
		}
	}
	writeJSON(w, 200, snapshot)
}
