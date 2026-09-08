#include <cmath>
#include <limits>
#include <string>
#include <vector>

#include "gtest/gtest.h"
#include "dwb_critics/obstacle_footprint.hpp"
#include "dwb_critics/rotate_to_goal.hpp"
#include "dwb_plugins/standard_traj_generator.hpp"
#include "pluginlib/class_loader.hpp"
#include "tangying_dwb_critics/approach_tracking.hpp"
#include "tangying_dwb_critics/continuous_goal.hpp"

using dwb_core::IllegalTrajectoryException;
using dwb_msgs::msg::Trajectory2D;
using nav_2d_msgs::msg::Path2D;
using nav_2d_msgs::msg::Twist2D;
using geometry_msgs::msg::Pose2D;

namespace
{
Pose2D point(double x, double y = 0, double theta = 0)
{
  Pose2D pose;
  pose.x = x;
  pose.y = y;
  pose.theta = theta;
  return pose;
}
Path2D path(const Pose2D & start, const Pose2D & goal)
{
  Path2D result;
  for (int i = 0; i <= 40; ++i) {
    const double t = i / 40.0;
    result.poses.push_back(point(
        start.x * (1 - t) + goal.x * t, start.y * (1 - t) + goal.y * t));
  }
  return result;
}
}  // namespace

class ApproachTrackingTest : public ::testing::Test
{
protected:
  void SetUp() override
  {
    rclcpp::init(0, nullptr);
    node = std::make_shared<nav2_util::LifecycleNode>("approach_tracking_test");
    costmap = std::make_shared<nav2_costmap_2d::Costmap2DROS>("approach_test_costmap");
    costmap->set_parameter(rclcpp::Parameter("plugins", std::vector<std::string>{}));
    costmap->set_parameter(rclcpp::Parameter("track_unknown_space", true));
    costmap->set_parameter(rclcpp::Parameter("origin_x", -2.0));
    costmap->set_parameter(rclcpp::Parameter("origin_y", -2.0));
    costmap->set_parameter(rclcpp::Parameter("resolution", .025));
    costmap->set_parameter(rclcpp::Parameter(
        "footprint", "[[-0.24,-0.23],[0.22,-0.23],[0.22,0.21],[-0.24,0.21]]"));
    ASSERT_EQ(costmap->on_configure(rclcpp_lifecycle::State()), nav2_util::CallbackReturn::SUCCESS);
    auto * grid = costmap->getCostmap();
    // Deliberately measured/free test fixture. Production unknown handling is
    // exercised separately below and is never changed by the tracking critics.
    std::fill_n(grid->getCharMap(), grid->getSizeInCellsX() * grid->getSizeInCellsY(), 0);
    for (const auto & ns : {"FollowPath", "Original"}) {
      for (const auto & name : names) {
        const std::string prefix = std::string(ns) + "." + name;
        node->declare_parameter(prefix + ".scale", name.find("Goal") == 0 ? 12.0 : 16.0);
        node->declare_parameter(prefix + ".forward_point_distance", .05);
      }
    }
    node->declare_parameter("FollowPath.ContinuousGoal.scale", 6.0);
    node->declare_parameter("FollowPath.ContinuousGoal.activation_distance", .15);
    node->declare_parameter("FollowPath.ContinuousGoal.lookahead_time", .5);
    node->declare_parameter("FollowPath.sim_time", 1.5);
    node->declare_parameter("FollowPath.linear_granularity", .01);
    node->declare_parameter("FollowPath.angular_granularity", .025);
    node->declare_parameter("FollowPath.acc_lim_x", .1);
    node->declare_parameter("FollowPath.acc_lim_y", .1);
    node->declare_parameter("FollowPath.acc_lim_theta", .4);
    node->declare_parameter("FollowPath.decel_lim_x", -.1);
    node->declare_parameter("FollowPath.decel_lim_y", -.1);
    node->declare_parameter("FollowPath.decel_lim_theta", -.4);
    generator.initialize(node, "FollowPath");
    continuous.initialize(node, "ContinuousGoal", "FollowPath", costmap);
    loader = std::make_unique<pluginlib::ClassLoader<dwb_core::TrajectoryCritic>>(
      "dwb_core", "dwb_core::TrajectoryCritic");
    for (const auto & name : names) {
      auto adapted = loader->createSharedInstance("tangying_dwb_critics::Approach" + name + "Critic");
      adapted->initialize(node, name, "FollowPath", costmap);
      wrappers.push_back(adapted);
      auto original = loader->createSharedInstance("dwb_critics::" + name + "Critic");
      original->initialize(node, name, "Original", costmap);
      originals.push_back(original);
    }
  }
  void TearDown() override
  {
    // Stop this test context before joining Costmap2DROS's private executor.
    // A very fast fixture can otherwise cancel it before its new thread starts
    // spinning, leaving cleanup waiting on an executor that missed cancellation.
    rclcpp::shutdown();
    wrappers.clear();
    originals.clear();
    loader.reset();
    costmap->on_cleanup(rclcpp_lifecycle::State());
    costmap.reset();
    node.reset();
  }
  double combined(const Trajectory2D & trajectory, bool adapted)
  {
    double total = continuous.scoreTrajectory(trajectory) * continuous.getScale();
    for (const auto & critic : adapted ? wrappers : originals) {
      if (critic->getScale() != 0) {
        total += critic->scoreTrajectory(trajectory) * critic->getScale();
      }
    }
    return total;
  }
  const std::vector<std::string> names{"GoalAlign", "PathAlign", "PathDist", "GoalDist"};
  nav2_util::LifecycleNode::SharedPtr node;
  std::shared_ptr<nav2_costmap_2d::Costmap2DROS> costmap;
  std::unique_ptr<pluginlib::ClassLoader<dwb_core::TrajectoryCritic>> loader;
  std::vector<dwb_core::TrajectoryCritic::Ptr> wrappers, originals;
  dwb_plugins::StandardTrajectoryGenerator generator;
  tangying_dwb_critics::ContinuousGoalCritic continuous;
};

TEST_F(ApproachTrackingTest, ResolvesRealGeneratorCrossCellCompetitionWithoutIncreasingWeight)
{
  for (bool lateral : {false, true}) {
    const auto start = lateral ? point(0, .0342, M_PI_2) : point(.0342);
    const auto goal = lateral ? point(0, .0428, M_PI_2) : point(.0428);
    const auto plan = path(start, goal);
    ASSERT_TRUE(continuous.prepare(start, Twist2D{}, goal, plan));
    for (size_t i = 0; i < wrappers.size(); ++i) {
      ASSERT_TRUE(wrappers[i]->prepare(start, Twist2D{}, goal, plan));
      ASSERT_TRUE(originals[i]->prepare(start, Twist2D{}, goal, plan));
    }
    const auto stopped = generator.generateTrajectory(start, Twist2D{}, Twist2D{});
    Twist2D velocity;
    velocity.x = .0125;
    const auto forward = generator.generateTrajectory(start, Twist2D{}, velocity);
    ASSERT_GT(forward.poses.size(), forward.time_offsets.size());
    EXPECT_LT(continuous.scoreTrajectory(forward), continuous.scoreTrajectory(stopped));
    // This reproduces the observed bug with real upstream grid critics: the
    // endpoint crosses a 25 mm cell while the 0.5 s point approaches the goal.
    EXPECT_GT(combined(forward, false), combined(stopped, false));
    EXPECT_LT(combined(forward, true), combined(stopped, true));
    EXPECT_DOUBLE_EQ(continuous.getScale(), 6.0);
  }
}

TEST_F(ApproachTrackingTest, FarFieldScoresAndScalesMatchAllFourUpstreamCritics)
{
  for (const double distance : {.15001, .65}) {
    const auto start = point(0);
    const auto goal = point(distance);
    const auto plan = path(start, goal);
    for (size_t i = 0; i < wrappers.size(); ++i) {
      ASSERT_TRUE(wrappers[i]->prepare(start, Twist2D{}, goal, plan));
      ASSERT_TRUE(originals[i]->prepare(start, Twist2D{}, goal, plan));
      EXPECT_DOUBLE_EQ(wrappers[i]->getScale(), originals[i]->getScale());
      for (const double vx : {-.025, 0.0, .0125, .05}) {
        Twist2D velocity;
        velocity.x = vx;
        const auto candidate = generator.generateTrajectory(start, Twist2D{}, velocity);
        EXPECT_DOUBLE_EQ(wrappers[i]->scoreTrajectory(candidate), originals[i]->scoreTrajectory(candidate));
      }
    }
  }
}

TEST_F(ApproachTrackingTest, SharesExactPhaseBoundaryAndDoesNotReuseInvalidPreparation)
{
  const auto stopped = generator.generateTrajectory(point(0), Twist2D{}, Twist2D{});
  for (const double distance : {.15, .15001, .14999, .15001, -.14999}) {
    const auto goal = point(distance);
    ASSERT_TRUE(continuous.prepare(point(0), Twist2D{}, goal, path(point(0), goal)));
    const bool approaching = std::abs(distance) <= .15;
    EXPECT_EQ(continuous.scoreTrajectory(stopped) > 0, approaching);
    for (const auto & critic : wrappers) {
      ASSERT_TRUE(critic->prepare(point(0), Twist2D{}, goal, path(point(0), goal)));
      EXPECT_EQ(critic->getScale() == 0, approaching);
      if (approaching) {EXPECT_DOUBLE_EQ(critic->scoreTrajectory(stopped), 0);}
      critic->reset();
      EXPECT_GT(critic->getScale(), 0);
      EXPECT_THROW(critic->scoreTrajectory(stopped), IllegalTrajectoryException);
      auto invalid_goal = goal;
      invalid_goal.x = std::numeric_limits<double>::quiet_NaN();
      EXPECT_FALSE(critic->prepare(point(0), Twist2D{}, invalid_goal, path(point(0), goal)));
      EXPECT_GT(critic->getScale(), 0);
      EXPECT_THROW(critic->scoreTrajectory(stopped), IllegalTrajectoryException);
      EXPECT_FALSE(critic->prepare(point(0), Twist2D{}, goal, Path2D{}));
      EXPECT_THROW(critic->scoreTrajectory(stopped), IllegalTrajectoryException);
    }
  }
}

TEST_F(ApproachTrackingTest, NearGoalBehindRobotStillHasTranslationAndFinalYawGradients)
{
  const auto start = point(-.0342);
  const auto goal = point(-.0428, 0, .2);
  const auto plan = path(start, goal);
  ASSERT_TRUE(continuous.prepare(start, Twist2D{}, goal, plan));
  for (const auto & critic : wrappers) {ASSERT_TRUE(critic->prepare(start, Twist2D{}, goal, plan));}
  const auto stopped = generator.generateTrajectory(start, Twist2D{}, Twist2D{});
  Twist2D backward;
  backward.x = -.0125;
  EXPECT_LT(combined(generator.generateTrajectory(start, Twist2D{}, backward), true), combined(stopped, true));

  node->declare_parameter("FollowPath.xy_goal_tolerance", .005);
  node->declare_parameter("FollowPath.RotateToGoal.lookahead_time", -1.0);
  dwb_critics::RotateToGoalCritic yaw;
  yaw.initialize(node, "RotateToGoal", "FollowPath", costmap);
  const auto arrived = point(goal.x);
  ASSERT_TRUE(yaw.prepare(arrived, Twist2D{}, goal, plan));
  Twist2D rotate;
  rotate.theta = .1;
  EXPECT_LT(yaw.scoreTrajectory(generator.generateTrajectory(arrived, Twist2D{}, rotate)),
    yaw.scoreTrajectory(generator.generateTrajectory(arrived, Twist2D{}, Twist2D{})));
}

TEST_F(ApproachTrackingTest, SoftPhaseCannotAuthorizeUnknownOrLethalFullFootprint)
{
  const auto start = point(.0342);
  const auto goal = point(.0428);
  for (const auto & critic : wrappers) {
    ASSERT_TRUE(critic->prepare(start, Twist2D{}, goal, path(start, goal)));
    EXPECT_DOUBLE_EQ(critic->getScale(), 0);
  }
  dwb_critics::ObstacleFootprintCritic collision;
  collision.initialize(node, "ObstacleFootprint", "FollowPath", costmap);
  ASSERT_TRUE(collision.prepare(start, Twist2D{}, goal, path(start, goal)));
  const auto candidate = generator.generateTrajectory(start, Twist2D{}, Twist2D{});
  EXPECT_NO_THROW(collision.scoreTrajectory(candidate));
  auto * grid = costmap->getCostmap();
  Twist2D velocity;
  velocity.x = .0125;
  const auto forward = generator.generateTrajectory(start, Twist2D{}, velocity);
  double front_offset = 0;
  for (const auto & vertex : costmap->getRobotFootprint()) {
    front_offset = std::max(front_offset, vertex.x);
  }
  unsigned int start_x, end_x, ignored_y;
  ASSERT_TRUE(grid->worldToMap(start.x + front_offset, 0, start_x, ignored_y));
  ASSERT_TRUE(grid->worldToMap(forward.poses.back().x + front_offset, 0, end_x, ignored_y));
  ASSERT_GT(end_x, start_x);
  for (const unsigned char blocked : {nav2_costmap_2d::NO_INFORMATION, nav2_costmap_2d::LETHAL_OBSTACLE}) {
    std::fill_n(grid->getCharMap(), grid->getSizeInCellsX() * grid->getSizeInCellsY(), 0);
    for (unsigned int y = 0; y < grid->getSizeInCellsY(); ++y) {grid->setCost(end_x, y, blocked);}
    // The robot's current full footprint is clear; only the end of its 1.5 s
    // prediction enters the blocked column. Short soft scoring cannot allow it.
    EXPECT_NO_THROW(collision.scoreTrajectory(candidate));
    EXPECT_THROW(collision.scoreTrajectory(forward), IllegalTrajectoryException);
  }
}
