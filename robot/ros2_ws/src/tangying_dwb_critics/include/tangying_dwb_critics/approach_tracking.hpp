#ifndef TANGYING_DWB_CRITICS__APPROACH_TRACKING_HPP_
#define TANGYING_DWB_CRITICS__APPROACH_TRACKING_HPP_

#include <cmath>
#include <stdexcept>

#include "dwb_core/exceptions.hpp"
#include "dwb_critics/goal_align.hpp"
#include "dwb_critics/path_align.hpp"
#include "nav2_util/node_utils.hpp"

namespace tangying_dwb_critics
{
// Coordinate the four soft grid objectives with ContinuousGoal. This does not
// establish free space: ObstacleFootprint must still score the entire trajectory.
template<class Upstream>
class ApproachTrackingCritic : public Upstream
{
public:
  void onInit() override
  {
    auto node = this->node_.lock();
    if (!node) {throw std::runtime_error("ApproachTracking node unavailable");}
    const auto key = this->dwb_plugin_name_ + ".ContinuousGoal.activation_distance";
    nav2_util::declare_parameter_if_not_declared(node, key, rclcpp::ParameterValue(0.15));
    node->get_parameter(key, activation_distance_);
    if (!std::isfinite(activation_distance_) || activation_distance_ <= 0) {
      throw std::invalid_argument("ApproachTracking activation distance must be positive");
    }
    Upstream::onInit();
    initialized_ = true;
    reset();
  }

  void reset() override
  {
    prepared_ = false;
    approaching_ = false;
    if (initialized_) {Upstream::reset();}
  }

  bool prepare(
    const geometry_msgs::msg::Pose2D & pose, const nav_2d_msgs::msg::Twist2D & velocity,
    const geometry_msgs::msg::Pose2D & goal, const nav_2d_msgs::msg::Path2D & plan) override
  {
    prepared_ = false;
    approaching_ = false;
    if (!initialized_ || !finitePose(pose) || !finitePose(goal) || plan.poses.empty() ||
      !std::isfinite(velocity.x) || !std::isfinite(velocity.y) ||
      !std::isfinite(velocity.theta))
    {
      return false;
    }
    for (const auto & point : plan.poses) {
      if (!finitePose(point)) {return false;}
    }
    const double distance = std::hypot(goal.x - pose.x, goal.y - pose.y);
    if (!std::isfinite(distance)) {return false;}
    approaching_ = distance <= activation_distance_;
    // Base prepare may call virtual reset(). Set the validity only after it
    // returns, and never reuse a grid from a failed preparation or older goal.
    prepared_ = approaching_ || Upstream::prepare(pose, velocity, goal, plan);
    return prepared_;
  }

  double scoreTrajectory(const dwb_msgs::msg::Trajectory2D & trajectory) override
  {
    if (!prepared_ || trajectory.poses.empty() ||
      !std::isfinite(trajectory.velocity.x) || !std::isfinite(trajectory.velocity.y) ||
      !std::isfinite(trajectory.velocity.theta))
    {
      throw dwb_core::IllegalTrajectoryException(this->name_, "Invalid tracking preparation or trajectory.");
    }
    for (const auto & point : trajectory.poses) {
      if (!finitePose(point)) {
        throw dwb_core::IllegalTrajectoryException(this->name_, "Invalid tracking trajectory pose.");
      }
    }
    return approaching_ ? 0.0 : Upstream::scoreTrajectory(trajectory);
  }

  double getScale() const override
  {
    // DWB may skip zero-scale critics. An invalid prepare must not preserve a
    // prior PathAlign zero scale and evade scoreTrajectory's explicit refusal.
    if (!prepared_) {return 1.0;}
    return approaching_ ? 0.0 : Upstream::getScale();
  }

  void addCriticVisualization(
    std::vector<std::pair<std::string, std::vector<float>>> & channels) override
  {
    if (prepared_ && !approaching_) {Upstream::addCriticVisualization(channels);}
  }

private:
  static bool finitePose(const geometry_msgs::msg::Pose2D & pose)
  {
    return std::isfinite(pose.x) && std::isfinite(pose.y) && std::isfinite(pose.theta);
  }
  bool initialized_{false};
  bool prepared_{false};
  bool approaching_{false};
  double activation_distance_{0.15};
};

class ApproachGoalAlignCritic : public ApproachTrackingCritic<dwb_critics::GoalAlignCritic> {};
class ApproachPathAlignCritic : public ApproachTrackingCritic<dwb_critics::PathAlignCritic> {};
class ApproachPathDistCritic : public ApproachTrackingCritic<dwb_critics::PathDistCritic> {};
class ApproachGoalDistCritic : public ApproachTrackingCritic<dwb_critics::GoalDistCritic> {};
}  // namespace tangying_dwb_critics
#endif
