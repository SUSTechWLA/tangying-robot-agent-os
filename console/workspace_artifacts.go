package console

import "net/http"

// Old file exports without provenance remain on disk but cannot become the
// current robot's calibration. Runtime service results use the bound client.
func (s *Server) allowWorkspaceCalibration(w http.ResponseWriter, document map[string]any) bool {
	if s.workspaceRobotID == "" {
		return true
	}
	robotID, _ := document["robotId"].(string)
	if robotID == "" || robotID != s.workspaceRobotID {
		writeJSON(w, http.StatusOK, map[string]any{
			"available": false,
			"reason":    "该标定文件没有当前机器人的身份依据，请检查本机器人的标定产物配置。",
			"code":      "CALIBRATION_ROBOT_MISMATCH",
		})
		return false
	}
	return true
}
