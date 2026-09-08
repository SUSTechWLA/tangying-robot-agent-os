#ifndef TANGYING_DWB_CRITICS__CONTINUOUS_GOAL_HPP_
#define TANGYING_DWB_CRITICS__CONTINUOUS_GOAL_HPP_

#include "dwb_core/trajectory_critic.hpp"

namespace tangying_dwb_critics
{
class ContinuousGoalCritic : public dwb_core::TrajectoryCritic
{
public:
  void onInit() override;
  void reset() override {prepared_ = false;}
  bool prepare(
    const geometry_msgs::msg::Pose2D & pose, const nav_2d_msgs::msg::Twist2D &,
    const geometry_msgs::msg::Pose2D & goal, const nav_2d_msgs::msg::Path2D &) override;
  double scoreTrajectory(const dwb_msgs::msg::Trajectory2D & trajectory) override;

private:
  geometry_msgs::msg::Pose2D goal_;
  bool prepared_{false};
  bool active_{false};
  double activation_distance_{0.15};
  double lookahead_time_{0.5};
};
}  // namespace tangying_dwb_critics
#endif
