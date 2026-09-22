// Simulation suction, not a model of vacuum pressure or finger contact.
// A fixed physics joint preserves the acquired relative pose. No pose is reset.
#include <chrono>
#include <cmath>
#include <map>
#include <mutex>
#include <string>
#include <gz/msgs/stringmsg.pb.h>
#include <gz/plugin/Register.hh>
#include <gz/sim/Model.hh>
#include <gz/sim/System.hh>
#include <gz/sim/Util.hh>
#include <gz/sim/components/DetachableJoint.hh>
#include <gz/sim/components/Model.hh>
#include <gz/sim/components/Name.hh>
#include <gz/transport/Node.hh>
#include <nlohmann/json.hpp>

namespace tangying {
using Json = nlohmann::json;
using namespace gz::sim;

class Suction final : public System, public ISystemConfigure,
                     public ISystemPreUpdate, public ISystemPostUpdate {
 public:
  void Configure(const Entity &entity, const std::shared_ptr<const sdf::Element> &,
                 EntityComponentManager &ecm, EventManager &) override {
    robot = Model(entity);
    for (const auto &side : {"left", "right"}) {
      tips[side] = robot.LinkByName(ecm, std::string(side) + "_arm_link6");
    }
    pub = transport.Advertise<gz::msgs::StringMsg>("/tangying/suction/state");
    transport.Subscribe("/tangying/suction/command", &Suction::Request, this);
  }

  void Request(const gz::msgs::StringMsg &msg) {
    try {
      auto request = Json::parse(msg.data());
      if (!request.is_object() || !request.contains("id") ||
          !request["id"].is_number_unsigned()) return;
      std::lock_guard<std::mutex> lock(mutex);
      // Monotonic IDs plus expiry prevent queued attach commands from taking
      // effect after a cancel/stop has already invalidated them.
      if (request.at("id").get<uint64_t>() > acceptedId &&
          (pending.is_null() || request["id"] > pending["id"])) pending = request;
    } catch (const Json::exception &) { /* Malformed commands never actuate. */ }
  }

  void PreUpdate(const UpdateInfo &info, EntityComponentManager &ecm) override {
    if (info.paused) return;
    for (const auto &name : {"red_cup", "blue_bottle", "tray_floor", "delivery_tray"}) {
      auto entity = ecm.EntityByComponents(components::Model(), components::Name(name));
      if (entity != kNullEntity) objects[name] = entity;
    }
    Json request;
    {
      std::lock_guard<std::mutex> lock(mutex);
      request.swap(pending);
      if (!request.is_null()) acceptedId = request.at("id").get<uint64_t>();
    }
    if (request.is_null()) return;
    try {
      const auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count();
      code = "COMMAND_EXPIRED";
      if (request.at("expiresMs").get<int64_t>() < now) return;
      const auto op = request.at("op").get<std::string>();
      code = "OK";
      if (op == "hold") return;  // Keep held objects attached during a stop.
      if (op == "detach") {
        if (joint != kNullEntity) ecm.RequestRemoveEntity(joint);
        joint = kNullEntity;
        held.clear();
        side.clear();
        return;
      }
      code = "INVALID_SUCTION_COMMAND";
      if (op != "attach") return;
      const auto target = request.at("object").get<std::string>();
      const auto arm = request.at("side").get<std::string>();
      if ((target != "red_cup" && target != "blue_bottle") || !tips.count(arm) ||
          tips.at(arm) == kNullEntity || !objects.count(target)) return;
      code = "GRIPPER_OCCUPIED";
      if (joint != kNullEntity) return;
      auto child = Model(objects.at(target)).CanonicalLink(ecm);
      code = "GRASP_MISS";
      if (child == kNullEntity ||
          (worldPose(child, ecm).Pos() - worldPose(tips.at(arm), ecm).Pos()).Length() > .09) return;
      joint = ecm.CreateEntity();
      ecm.CreateComponent(joint, components::DetachableJoint({tips.at(arm), child, "fixed"}));
      held = target;
      side = arm;
      code = "OK";
    } catch (const Json::exception &) { code = "INVALID_SUCTION_COMMAND"; }
  }

  void PostUpdate(const UpdateInfo &info, const EntityComponentManager &ecm) override {
    if (info.paused || info.simTime - lastPublish < std::chrono::milliseconds(50)) return;
    lastPublish = info.simTime;
    Json state = {{"schemaVersion", "gazebo.suction.v1"}, {"mode", "sim_suction"},
                  {"sequence", ++sequence}, {"simTimeNs", info.simTime.count()},
                  {"commandId", acceptedId}, {"code", code}, {"held", held},
                  {"side", side}, {"attached", joint != kNullEntity &&
                     ecm.Component<components::DetachableJoint>(joint) != nullptr},
                  {"robotPose", Pose(worldPose(robot.Entity(), ecm))},
                  {"objects", Json::object()}, {"tips", Json::object()}};
    for (auto const &[name, entity] : objects) state["objects"][name] = Pose(worldPose(entity, ecm));
    for (auto const &[name, entity] : tips) {
      if (entity != kNullEntity) state["tips"][name] = Pose(worldPose(entity, ecm));
    }
    gz::msgs::StringMsg msg;
    msg.set_data(state.dump());
    pub.Publish(msg);
  }

 private:
  static Json Pose(const gz::math::Pose3d &p) {
    return {p.X(), p.Y(), p.Z(), p.Rot().W(), p.Rot().X(), p.Rot().Y(), p.Rot().Z()};
  }
  Model robot{kNullEntity};
  Entity joint{kNullEntity};
  std::map<std::string, Entity> objects, tips;
  gz::transport::Node transport;
  gz::transport::Node::Publisher pub;
  std::mutex mutex;
  Json pending;
  uint64_t acceptedId{0}, sequence{0};
  std::string held, side, code{"READY"};
  std::chrono::steady_clock::duration lastPublish{};
};
}  // namespace tangying
GZ_ADD_PLUGIN(tangying::Suction, gz::sim::System, gz::sim::ISystemConfigure,
              gz::sim::ISystemPreUpdate, gz::sim::ISystemPostUpdate)
