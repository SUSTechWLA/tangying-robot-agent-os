// Simple classical force controller for the field integration fixture.
// This is not a vendor flight controller, a wheel traction or a legged gait model.
// No pose or velocity components are assigned: all motion is integrated by physics.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <mutex>
#include <stdexcept>
#include <string>
#include <gz/msgs/stringmsg.pb.h>
#include <gz/msgs/twist.pb.h>
#include <gz/msgs/boolean.pb.h>
#include <gz/plugin/Register.hh>
#include <gz/sim/System.hh>
#include <gz/sim/Model.hh>
#include <gz/sim/Link.hh>
#include <gz/sim/Util.hh>
#include <gz/transport/Node.hh>
#include <nlohmann/json.hpp>

namespace tangying {
// Full 3D enclosing spheres, valid even when a body tilts. Ground box half
// extents (.25, .20, .15) require sqrt(.125)=.353554 m, not its XY footprint.
constexpr double kGroundBoundingRadius = .36;
constexpr double kAerialBoundingRadius = .34;

class FieldController final : public gz::sim::System,
    public gz::sim::ISystemConfigure, public gz::sim::ISystemPreUpdate,
    public gz::sim::ISystemPostUpdate {
 public:
  void Configure(const gz::sim::Entity &entity,
                 const std::shared_ptr<const sdf::Element> &sdf,
                 gz::sim::EntityComponentManager &ecm, gz::sim::EventManager &) override {
    const gz::sim::Model robot(entity);
    name = robot.Name(ecm);
    aerial = sdf->Get<bool>("aerial", false).first;
    mass = sdf->Get<double>("mass", aerial ? 1.2 : 12.).first;
    lease = sdf->Get<double>("lease_seconds", .4).first;
    if (!std::isfinite(mass) || mass <= 0 || !std::isfinite(lease) || lease <= 0 || lease > 2)
      throw std::runtime_error("invalid field controller commissioning parameters");
    link = gz::sim::Link(robot.LinkByName(ecm, "base_link"));
    if (!link.Valid(ecm)) throw std::runtime_error("field model missing base_link");
    link.EnableVelocityChecks(ecm);
    node.Subscribe("/field/" + name + "/cmd_vel", &FieldController::Command, this);
    node.Subscribe("/field/" + name + "/stop", &FieldController::Stop, this);
    state = node.Advertise<gz::msgs::StringMsg>("/field/" + name + "/state");
  }

  void Command(const gz::msgs::Twist &msg) {
    std::lock_guard<std::mutex> lock(mutex);
    const gz::math::Vector3d requested(msg.linear().x(), msg.linear().y(), msg.linear().z());
    if (!requested.IsFinite() || !std::isfinite(msg.angular().z()) ||
        requested.Length() > (aerial ? 1.0 : .8) + 1e-9 ||
        std::abs(msg.angular().z()) > 1 || (!aerial && std::abs(requested.Z()) > 1e-9)) {
      target.Set(0, 0, 0); yawRate = 0; braking = true; reason = "invalid_command";
      return;
    }
    target = requested; yawRate = msg.angular().z(); received = now;
    braking = false; reason = "active_command";
  }

  void Stop(const gz::msgs::Boolean &msg) {
    if (!msg.data()) return;
    std::lock_guard<std::mutex> lock(mutex);
    target.Set(0, 0, 0); yawRate = 0; braking = true; reason = "requested_stop";
  }

  void PreUpdate(const gz::sim::UpdateInfo &info,
                 gz::sim::EntityComponentManager &ecm) override {
    if (info.paused) return;
    std::lock_guard<std::mutex> lock(mutex);
    now = std::chrono::duration<double>(info.simTime).count();
    if (now < previous) {target.Set(0, 0, 0); braking = true; reason = "clock_reset";}
    previous = now;
    if (!braking && now - received >= lease) {
      target.Set(0, 0, 0); yawRate = 0; braking = true; reason = "command_watchdog_expired";
    }
    const auto v = link.WorldLinearVelocity(ecm);
    const auto w = link.WorldAngularVelocity(ecm);
    if (!v || !w) return;
    const auto pose = gz::sim::worldPose(link.Entity(), ecm);
    const double dt = std::chrono::duration<double>(info.dt).count();
    yaw += yawRate * dt;
    gz::math::Vector3d acceleration;
    for (int i = 0; i < 3; ++i)
      acceleration[i] = std::clamp((aerial ? 2.5 : 6.) * (target[i] - (*v)[i]), -2., 2.);
    gz::math::Vector3d force, torque;
    if (!aerial) {
      force.Set(mass * acceleration.X(), mass * acceleration.Y(), 0);
      torque.Set(0, 0, std::clamp(3. * (yawRate - w->Z()), -3., 3.));
    } else {
      const gz::math::Vector3d desiredForce = mass * (acceleration + gz::math::Vector3d(0, 0, 9.81));
      const auto desiredZ = desiredForce.Normalized();
      const gz::math::Vector3d heading(std::cos(yaw), std::sin(yaw), 0);
      const auto desiredY = desiredZ.Cross(heading).Normalized();
      const auto desiredX = desiredY.Cross(desiredZ);
      const auto x = pose.Rot().RotateVector(gz::math::Vector3d::UnitX);
      const auto y = pose.Rot().RotateVector(gz::math::Vector3d::UnitY);
      const auto z = pose.Rot().RotateVector(gz::math::Vector3d::UnitZ);
      torque = (x.Cross(desiredX) + y.Cross(desiredY) + z.Cross(desiredZ)) - .35 * (*w);
      for (int i = 0; i < 3; ++i) torque[i] = std::clamp(torque[i], -.8, .8);
      force = z * std::clamp(desiredForce.Dot(z), 0., 24.);
    }
    appliedForce = force; appliedTorque = torque;
    link.AddWorldWrench(ecm, force, torque);
  }

  void PostUpdate(const gz::sim::UpdateInfo &info,
                  const gz::sim::EntityComponentManager &ecm) override {
    if (info.paused) return;
    std::lock_guard<std::mutex> lock(mutex);
    const double t = std::chrono::duration<double>(info.simTime).count();
    if (t - lastPublished < .02 && t >= lastPublished) return;
    lastPublished = t;
    const auto v = link.WorldLinearVelocity(ecm);
    const auto w = link.WorldAngularVelocity(ecm);
    if (!v || !w) return;
    const auto pose = gz::sim::worldPose(link.Entity(), ecm);
    const auto xyz = [](const gz::math::Vector3d &x) {
      return nlohmann::json::array({x.X(), x.Y(), x.Z()});
    };
    nlohmann::json result = {
      {"robotId", name}, {"simulationTime", t}, {"clockDomain", "simulation"},
      {"position", xyz(pose.Pos())}, {"linearVelocity", xyz(*v)}, {"angularVelocity", xyz(*w)},
      {"quaternionWXYZ", {pose.Rot().W(), pose.Rot().X(), pose.Rot().Y(), pose.Rot().Z()}},
      {"yaw", pose.Rot().Yaw()}, {"speed", v->Length()},
      {"worldId", "field-validation"}, {"frameId", "world"},
      {"radius", aerial ? kAerialBoundingRadius : kGroundBoundingRadius},
      {"stopRequested", braking}, {"stopReason", reason},
      {"stopMode", aerial ? "hover" : "brake"}, {"physicalStopVerified", false},
      {"force", xyz(appliedForce)}, {"torque", xyz(appliedTorque)},
      {"commandAge", now - received}, {"leaseSeconds", lease},
      {"modelKind", aerial ? "generic_thrust_rigid_body" : "generic_force_ground_body"}
    };
    gz::msgs::StringMsg msg; msg.set_data(result.dump()); state.Publish(msg);
  }

 private:
  gz::sim::Link link;
  gz::transport::Node node;
  gz::transport::Node::Publisher state;
  std::mutex mutex;
  std::string name, reason{"initial_hold"};
  bool aerial{false}, braking{true};
  double mass{12}, lease{.4}, now{0}, previous{0}, received{0}, yaw{0}, yawRate{0}, lastPublished{-1};
  gz::math::Vector3d target{}, appliedForce{}, appliedTorque{};
};
}
GZ_ADD_PLUGIN(tangying::FieldController, gz::sim::System, gz::sim::ISystemConfigure,
              gz::sim::ISystemPreUpdate, gz::sim::ISystemPostUpdate)
