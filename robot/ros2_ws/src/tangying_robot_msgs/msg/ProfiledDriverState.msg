string robot_id
string profile_revision
string commissioning_id
uint64 sequence
int64 observed_at_unix_ms
bool commissioned
bool armed
bool healthy
bool watchdog_active
string stop_procedure_id
uint32 command_loss_timeout_ms
float64 max_linear_speed_mps
float64 max_angular_speed_radps
string[] supported_tools
string[] faults
# Empty only when spatial navigation is not configured on both peers.
string spatial_navigation_revision
