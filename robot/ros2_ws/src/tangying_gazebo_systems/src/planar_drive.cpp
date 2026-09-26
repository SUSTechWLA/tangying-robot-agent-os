// Bounded velocity interface for the reference XLeRobot planar base.
// Contact geometry remains in Gazebo physics. This is not a wheel traction model.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <mutex>
#include <gz/msgs/twist.pb.h>
#include <gz/plugin/RegisterMore.hh>
#include <gz/sim/System.hh>
#include <gz/sim/Model.hh>
#include <gz/sim/Util.hh>
#include <gz/sim/components/JointVelocityCmd.hh>
#include <gz/sim/components/AngularVelocityCmd.hh>
#include <gz/sim/components/LinearVelocityCmd.hh>
#include <gz/transport/Node.hh>
namespace tangying {
class PlanarDrive final : public gz::sim::System, public gz::sim::ISystemConfigure,
                         public gz::sim::ISystemPreUpdate {
 public:
  void Configure(const gz::sim::Entity &entity, const std::shared_ptr<const sdf::Element> &,
                 gz::sim::EntityComponentManager &ecm, gz::sim::EventManager &) override {
    const gz::sim::Model robot(entity);
    slideX=robot.JointByName(ecm,"base_slide_x");slideY=robot.JointByName(ecm,"base_slide_y");
    yaw=robot.JointByName(ecm,"base_yaw");base=robot.LinkByName(ecm,"base_link");
    initialRotation=gz::sim::worldPose(entity,ecm).Rot();
    model = entity; node.Subscribe("/cmd_vel", &PlanarDrive::Command, this);
  }
  void Command(const gz::msgs::Twist &msg) {
    std::lock_guard<std::mutex> lock(mutex);
    double x=msg.linear().x(), y=msg.linear().y(), w=msg.angular().z();
    if (!std::isfinite(x)||!std::isfinite(y)||!std::isfinite(w)) x=y=w=0;
    const double norm=std::hypot(x,y), scale=norm>.35 ? .35/norm : 1.;
    linear={x*scale,y*scale,0};angular={0,0,std::clamp(w,-.8,.8)};
    received=std::chrono::steady_clock::now();
  }
  void PreUpdate(const gz::sim::UpdateInfo &info, gz::sim::EntityComponentManager &ecm) override {
    if(info.paused) return;
    std::lock_guard<std::mutex> lock(mutex);
    const bool fresh=std::chrono::steady_clock::now()-received<std::chrono::milliseconds(500);
    if(slideX!=gz::sim::kNullEntity && slideY!=gz::sim::kNullEntity && yaw!=gz::sim::kNullEntity) {
      const auto velocity=initialRotation.RotateVectorReverse(gz::sim::worldPose(base,ecm).Rot().RotateVector(linear));
      ecm.SetComponentData<gz::sim::components::JointVelocityCmd>(slideX,std::vector<double>{fresh?velocity.X():0.});
      ecm.SetComponentData<gz::sim::components::JointVelocityCmd>(slideY,std::vector<double>{fresh?velocity.Y():0.});
      ecm.SetComponentData<gz::sim::components::JointVelocityCmd>(yaw,std::vector<double>{fresh?angular.Z():0.});
      return;
    }
    ecm.SetComponentData<gz::sim::components::LinearVelocityCmd>(model,fresh?linear:gz::math::Vector3d::Zero);
    ecm.SetComponentData<gz::sim::components::AngularVelocityCmd>(model,fresh?angular:gz::math::Vector3d::Zero);
  }
 private:
  gz::sim::Entity slideX{gz::sim::kNullEntity},slideY{gz::sim::kNullEntity},yaw{gz::sim::kNullEntity},base{gz::sim::kNullEntity};
  gz::math::Quaterniond initialRotation;
  gz::sim::Entity model{gz::sim::kNullEntity};gz::transport::Node node;std::mutex mutex;
  gz::math::Vector3d linear{},angular{};std::chrono::steady_clock::time_point received{};
};
}
GZ_ADD_PLUGIN(tangying::PlanarDrive,gz::sim::System,gz::sim::ISystemConfigure,gz::sim::ISystemPreUpdate)
