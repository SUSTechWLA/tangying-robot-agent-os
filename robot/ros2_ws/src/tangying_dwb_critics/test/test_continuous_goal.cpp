#include <limits>

#include "gtest/gtest.h"
#include "dwb_core/exceptions.hpp"
#include "dwb_plugins/standard_traj_generator.hpp"
#include "pluginlib/class_loader.hpp"
#include "tangying_dwb_critics/continuous_goal.hpp"

using tangying_dwb_critics::ContinuousGoalCritic;
using dwb_core::IllegalTrajectoryException;
using dwb_msgs::msg::Trajectory2D;
using nav_2d_msgs::msg::Path2D;
using nav_2d_msgs::msg::Twist2D;

geometry_msgs::msg::Pose2D pose(double x, double y)
{
  geometry_msgs::msg::Pose2D result;
  result.x = x;
  result.y = y;
  return result;
}
dwb_msgs::msg::Trajectory2D trajectory(double x, double start_y, double end_y)
{
  dwb_msgs::msg::Trajectory2D result;
  result.poses = {pose(x, start_y), pose(x, end_y)};
  result.time_offsets.resize(2);
  result.time_offsets[1].sec = 1;
  result.time_offsets[1].nanosec = 500000000;
  return result;
}

TEST(ContinuousGoal, RetainsGradientWithinOneCoarseCostmapCell)
{
  ContinuousGoalCritic critic;
  ASSERT_TRUE(critic.prepare(pose(0, .0259), Twist2D{}, pose(0, .0421), Path2D{}));
  auto stopped = trajectory(0, .0259, .0259);
  auto forward = trajectory(0, .0259, .0559);
  EXPECT_NEAR(critic.scoreTrajectory(stopped), .0162, 1e-9);
  EXPECT_NEAR(critic.scoreTrajectory(forward), .0062, 1e-9);
  EXPECT_LT(critic.scoreTrajectory(forward), critic.scoreTrajectory(stopped));
}

TEST(ContinuousGoal, UsesMetricGoalArgumentAndBothCoordinates)
{
  ContinuousGoalCritic critic;
  nav_2d_msgs::msg::Path2D cropped;
  cropped.poses.push_back(pose(20, 30));
  ASSERT_TRUE(critic.prepare(pose(1, 2), Twist2D{}, pose(1.003, 2.004), cropped));
  EXPECT_NEAR(critic.scoreTrajectory(trajectory(1, 2, 2)), .005, 1e-9);
  EXPECT_DOUBLE_EQ(critic.scoreTrajectory(trajectory(1.003, 2.004, 2.004)), 0);
}

TEST(ContinuousGoal, LeavesDistantPathTrackingUnchanged)
{
  ContinuousGoalCritic critic;
  ASSERT_TRUE(critic.prepare(pose(0, -.6), Twist2D{}, pose(0, .05), Path2D{}));
  EXPECT_DOUBLE_EQ(critic.scoreTrajectory(trajectory(0, -.6, -.55)), 0);
}

TEST(ContinuousGoal, InvalidPreparationAndResetCannotReusePreviousGoal)
{
  ContinuousGoalCritic critic;
  auto valid = trajectory(0, 0, .01);
  EXPECT_THROW(critic.scoreTrajectory(valid), IllegalTrajectoryException);
  ASSERT_TRUE(critic.prepare(pose(0, 0), Twist2D{}, pose(0, .02), Path2D{}));
  EXPECT_GT(critic.scoreTrajectory(valid), 0);
  EXPECT_FALSE(critic.prepare(pose(0, 0), Twist2D{}, pose(0, std::numeric_limits<double>::quiet_NaN()), Path2D{}));
  EXPECT_THROW(critic.scoreTrajectory(valid), IllegalTrajectoryException);
  ASSERT_TRUE(critic.prepare(pose(0, 0), Twist2D{}, pose(0, .02), Path2D{}));
  critic.reset();
  EXPECT_THROW(critic.scoreTrajectory(valid), IllegalTrajectoryException);
}

TEST(ContinuousGoal, RejectsEmptyNonfiniteAndInvalidTimeTrajectories)
{
  ContinuousGoalCritic critic;
  ASSERT_TRUE(critic.prepare(pose(0, 0), Twist2D{}, pose(0, .02), Path2D{}));
  EXPECT_THROW(critic.scoreTrajectory(Trajectory2D{}), IllegalTrajectoryException);
  auto invalid = trajectory(0, 0, .01);
  invalid.poses.back().x = std::numeric_limits<double>::infinity();
  EXPECT_THROW(critic.scoreTrajectory(invalid), IllegalTrajectoryException);
  invalid = trajectory(0, 0, .01);
  invalid.time_offsets.clear();
  EXPECT_THROW(critic.scoreTrajectory(invalid), IllegalTrajectoryException);
  invalid = trajectory(0, 0, .01);
  invalid.time_offsets[1] = invalid.time_offsets[0];
  EXPECT_THROW(critic.scoreTrajectory(invalid), IllegalTrajectoryException);
  invalid = trajectory(0, 0, .01);
  invalid.velocity.x = std::numeric_limits<double>::quiet_NaN();
  EXPECT_THROW(critic.scoreTrajectory(invalid), IllegalTrajectoryException);
  // Disabled far-field scoring must still reject malformed trajectory input.
  ASSERT_TRUE(critic.prepare(pose(0, -.6), Twist2D{}, pose(0, .05), Path2D{}));
  EXPECT_THROW(critic.scoreTrajectory(invalid), IllegalTrajectoryException);
}

TEST(ContinuousGoal, InstalledPluginIsDiscoverableThroughDwbPluginlibContract)
{
  pluginlib::ClassLoader<dwb_core::TrajectoryCritic> loader(
    "dwb_core", "dwb_core::TrajectoryCritic");
  auto critic = loader.createSharedInstance("tangying_dwb_critics::ContinuousGoalCritic");
  ASSERT_TRUE(critic->prepare(pose(0, .0259), Twist2D{}, pose(0, .0421), Path2D{}));
  EXPECT_NEAR(critic->scoreTrajectory(trajectory(0, .0259, .0259)), .0162, 1e-9);
}

TEST(ContinuousGoal, AcceptsExactlyJazzyDuplicateEndpointAndRejectsOtherMismatches)
{
  ContinuousGoalCritic critic;
  ASSERT_TRUE(critic.prepare(pose(0, .0259), Twist2D{}, pose(0, .0421), Path2D{}));
  auto generated_shape = trajectory(0, .0259, .0559);
  generated_shape.poses.push_back(generated_shape.poses.back());
  EXPECT_NEAR(critic.scoreTrajectory(generated_shape), .0062, 1e-9);
  generated_shape.poses.back().y += .001;
  EXPECT_THROW(critic.scoreTrajectory(generated_shape), IllegalTrajectoryException);
  generated_shape.poses.push_back(generated_shape.poses.back());
  EXPECT_THROW(critic.scoreTrajectory(generated_shape), IllegalTrajectoryException);
}

TEST(ContinuousGoal, ScoresRealJazzyGeneratorStationaryTranslationAndRotation)
{
  rclcpp::init(0, nullptr);
  auto node = std::make_shared<nav2_util::LifecycleNode>("continuous_goal_generator_test");
  node->declare_parameter("FollowPath.sim_time", 1.5);
  node->declare_parameter("FollowPath.linear_granularity", .01);
  node->declare_parameter("FollowPath.angular_granularity", .025);
  node->declare_parameter("FollowPath.acc_lim_x", .1);
  node->declare_parameter("FollowPath.acc_lim_y", .1);
  node->declare_parameter("FollowPath.acc_lim_theta", .4);
  node->declare_parameter("FollowPath.decel_lim_x", -.1);
  node->declare_parameter("FollowPath.decel_lim_y", -.1);
  node->declare_parameter("FollowPath.decel_lim_theta", -.4);
  dwb_plugins::StandardTrajectoryGenerator generator;
  generator.initialize(node, "FollowPath");
  ContinuousGoalCritic critic;
  ASSERT_TRUE(critic.prepare(pose(.0259, 0), Twist2D{}, pose(.0421, 0), Path2D{}));
  nav_2d_msgs::msg::Twist2D command;
  const auto stopped = generator.generateTrajectory(pose(.0259, 0), Twist2D{}, command);
  ASSERT_EQ(stopped.poses.size(), stopped.time_offsets.size() + 1);
  EXPECT_NEAR(critic.scoreTrajectory(stopped), .0162, 1e-9);
  command.x = .0125;
  const auto forward = generator.generateTrajectory(pose(.0259, 0), Twist2D{}, command);
  EXPECT_LT(critic.scoreTrajectory(forward), critic.scoreTrajectory(stopped));
  ASSERT_TRUE(critic.prepare(pose(0, .0259), Twist2D{}, pose(0, .0421), Path2D{}));
  command.x = 0;
  command.y = .0125;
  const auto lateral = generator.generateTrajectory(pose(0, .0259), Twist2D{}, command);
  EXPECT_LT(critic.scoreTrajectory(lateral), .0162);
  ASSERT_TRUE(critic.prepare(pose(.0259, 0), Twist2D{}, pose(.0421, 0), Path2D{}));
  command.x = 0;
  command.y = 0;
  command.theta = .05;
  const auto rotated = generator.generateTrajectory(pose(.0259, 0), Twist2D{}, command);
  EXPECT_NEAR(critic.scoreTrajectory(rotated), .0162, 1e-9);
  node.reset();
  rclcpp::shutdown();
}
