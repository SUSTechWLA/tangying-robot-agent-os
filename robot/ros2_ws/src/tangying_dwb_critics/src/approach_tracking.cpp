#include "tangying_dwb_critics/approach_tracking.hpp"
#include "pluginlib/class_list_macros.hpp"

PLUGINLIB_EXPORT_CLASS(tangying_dwb_critics::ApproachGoalAlignCritic, dwb_core::TrajectoryCritic)
PLUGINLIB_EXPORT_CLASS(tangying_dwb_critics::ApproachPathAlignCritic, dwb_core::TrajectoryCritic)
PLUGINLIB_EXPORT_CLASS(tangying_dwb_critics::ApproachPathDistCritic, dwb_core::TrajectoryCritic)
PLUGINLIB_EXPORT_CLASS(tangying_dwb_critics::ApproachGoalDistCritic, dwb_core::TrajectoryCritic)
