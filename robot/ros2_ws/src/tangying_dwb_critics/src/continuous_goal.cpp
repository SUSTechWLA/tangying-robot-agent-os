#include "tangying_dwb_critics/continuous_goal.hpp"

#include <cmath>
#include <stdexcept>
#include <string>

#include "dwb_core/exceptions.hpp"
#include "nav2_util/node_utils.hpp"
#include "pluginlib/class_list_macros.hpp"

namespace tangying_dwb_critics
{
namespace
{
bool finitePose(const geometry_msgs::msg::Pose2D & pose)
{
  return std::isfinite(pose.x) && std::isfinite(pose.y) && std::isfinite(pose.theta);
}
}  // namespace

void ContinuousGoalCritic::onInit()
{
  auto node = node_.lock();
  if (!node) {throw std::runtime_error("ContinuousGoal node unavailable");}
  const std::string prefix = dwb_plugin_name_ + "." + name_ + ".";
  nav2_util::declare_parameter_if_not_declared(
    node, prefix + "activation_distance", rclcpp::ParameterValue(0.15));
  nav2_util::declare_parameter_if_not_declared(
    node, prefix + "lookahead_time", rclcpp::ParameterValue(0.5));
  node->get_parameter(prefix + "activation_distance", activation_distance_);
  node->get_parameter(prefix + "lookahead_time", lookahead_time_);
  if (!std::isfinite(activation_distance_) || activation_distance_ <= 0 ||
    !std::isfinite(lookahead_time_) || lookahead_time_ <= 0 ||
    !std::isfinite(scale_) || scale_ <= 0)
  {
    throw std::invalid_argument("ContinuousGoal parameters must be finite and positive");
  }
  reset();
}
bool ContinuousGoalCritic::prepare(
  const geometry_msgs::msg::Pose2D & pose, const nav_2d_msgs::msg::Twist2D &,
  const geometry_msgs::msg::Pose2D & goal, const nav_2d_msgs::msg::Path2D &)
{
  // DWB logs a false prepare result but still calls scoreTrajectory. Clear the
  // previous goal's validity first, and reject scoring until preparation works.
  prepared_ = false;
  active_ = false;
  if (!finitePose(pose) || !finitePose(goal)) {return false;}
  const double distance = std::hypot(goal.x - pose.x, goal.y - pose.y);
  if (!std::isfinite(distance)) {return false;}
  goal_ = goal;  // DWB already supplies pose, goal and trajectories in costmap frame.
  active_ = distance <= activation_distance_;
  prepared_ = true;
  return true;
}
double ContinuousGoalCritic::scoreTrajectory(const dwb_msgs::msg::Trajectory2D & trajectory)
{
  if (!prepared_ || trajectory.poses.empty() || trajectory.time_offsets.empty() ||
    !std::isfinite(trajectory.velocity.x) || !std::isfinite(trajectory.velocity.y) ||
    !std::isfinite(trajectory.velocity.theta))
  {
    throw dwb_core::IllegalTrajectoryException(name_, "Invalid goal or trajectory.");
  }
  const size_t timed_count = trajectory.time_offsets.size();
  if (trajectory.poses.size() != timed_count) {
    // Jazzy 1.3.12 StandardTrajectoryGenerator appends one duplicate final pose
    // without an extra timestamp when include_last_point=true. Accept exactly
    // that shape; arbitrary mismatched vectors must never reach interpolation.
    if (trajectory.poses.size() != timed_count + 1) {
      throw dwb_core::IllegalTrajectoryException(name_, "Mismatched trajectory times.");
    }
    const auto & last = trajectory.poses.back();
    const auto & timed_last = trajectory.poses[timed_count - 1];
    if (last.x != timed_last.x || last.y != timed_last.y || last.theta != timed_last.theta) {
      throw dwb_core::IllegalTrajectoryException(name_, "Untimed nonduplicate endpoint.");
    }
  }
  for (const auto & pose : trajectory.poses) {
    if (!finitePose(pose)) {
      throw dwb_core::IllegalTrajectoryException(name_, "Invalid trajectory pose.");
    }
  }
  double previous = -1;
  for (size_t index = 0; index < timed_count; ++index) {
    const auto & stamp = trajectory.time_offsets[index];
    const double seconds = stamp.sec + stamp.nanosec * 1e-9;
    if (stamp.sec < 0 ||
      stamp.nanosec >= 1000000000 || seconds <= previous)
    {
      throw dwb_core::IllegalTrajectoryException(name_, "Invalid trajectory pose or time.");
    }
    previous = seconds;
  }
  if (!active_) {return 0.0;}
  // Bound every access by the validated timed prefix. In Jazzy, projectPose()
  // indexes timestamps using poses.size(), including the untimed duplicate.
  auto projected = trajectory.poses[timed_count - 1];
  const auto seconds_at = [&trajectory](size_t index) {
      const auto & stamp = trajectory.time_offsets[index];
      return stamp.sec + stamp.nanosec * 1e-9;
    };
  if (lookahead_time_ <= seconds_at(0)) {
    projected = trajectory.poses[0];
  } else {
    for (size_t index = 1; index < timed_count; ++index) {
      if (lookahead_time_ <= seconds_at(index)) {
        const double ratio = (lookahead_time_ - seconds_at(index - 1)) /
          (seconds_at(index) - seconds_at(index - 1));
        const auto & start = trajectory.poses[index - 1];
        const auto & end = trajectory.poses[index];
        projected.x = start.x * (1 - ratio) + end.x * ratio;
        projected.y = start.y * (1 - ratio) + end.y * ratio;
        break;
      }
    }
  }
  const double distance = std::hypot(goal_.x - projected.x, goal_.y - projected.y);
  if (!std::isfinite(distance)) {
    throw dwb_core::IllegalTrajectoryException(name_, "Invalid projected goal distance.");
  }
  // This is only an additional objective in metres. It neither reads/writes the
  // costmap nor authorizes a trajectory rejected by DWB's collision critics.
  return distance;
}
}  // namespace tangying_dwb_critics

PLUGINLIB_EXPORT_CLASS(tangying_dwb_critics::ContinuousGoalCritic, dwb_core::TrajectoryCritic)
