// Local Gazebo transport recorder. Only the experiment driver sees oracle poses.
#include <gz/transport/Node.hh>
#include <gz/msgs/image.pb.h>
#include <gz/msgs/contacts.pb.h>
#include <gz/msgs/model.pb.h>
#include <gz/msgs/pose_v.pb.h>
#include <gz/msgs/world_control.pb.h>
#include <gz/msgs/boolean.pb.h>
#include <gz/msgs/double.pb.h>
#include <gz/msgs/world_stats.pb.h>
#include <gz/msgs/odometry.pb.h>
#include <gz/msgs/twist.pb.h>
#include <google/protobuf/util/json_util.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <thread>
using json = nlohmann::json;
using namespace std::chrono_literals;
std::mutex guard;
std::map<int64_t, gz::msgs::Image> images, depths;
gz::msgs::Contacts contacts;
gz::msgs::Model joints;
gz::msgs::Pose_V oracle;
std::condition_variable changed;
uint64_t iterations=0;
bool statsSeen=false;
gz::msgs::Odometry odometry;
void odom(const gz::msgs::Odometry &m) {std::lock_guard<std::mutex> lock(guard);odometry=m;}
void stats(const gz::msgs::WorldStatistics &m) {std::lock_guard<std::mutex> lock(guard);iterations=m.iterations();statsSeen=true;changed.notify_all();}
int64_t stamp(const gz::msgs::Header &h) {return h.stamp().sec()*1000000000LL+h.stamp().nsec();}
void image(const gz::msgs::Image &m) {std::lock_guard<std::mutex> lock(guard); images[stamp(m.header())]=m; while(images.size()>20) images.erase(images.begin()); changed.notify_all();}
void depth(const gz::msgs::Image &m) {std::lock_guard<std::mutex> lock(guard); depths[stamp(m.header())]=m; while(depths.size()>20) depths.erase(depths.begin()); changed.notify_all();}
void contact(const gz::msgs::Contacts &m) {std::lock_guard<std::mutex> lock(guard); contacts=m;}
void joint(const gz::msgs::Model &m) {std::lock_guard<std::mutex> lock(guard); joints=m;}
void pose(const gz::msgs::Pose_V &m) {std::lock_guard<std::mutex> lock(guard); oracle=m;}
void write(const std::string &p, const google::protobuf::Message &m) {std::string s; google::protobuf::util::MessageToJsonString(m,&s); std::ofstream(p)<<s;}
int main() {
 gz::transport::Node node;
 node.Subscribe("/gvf/camera/image",image); node.Subscribe("/gvf/camera/depth_image",depth);
 node.Subscribe("/world/tangying_home/model/gvf_gripper/link/left/sensor/contact/contact",contact); node.Subscribe("/gvf/joints",joint);
 node.Subscribe("/world/tangying_home/dynamic_pose/info",pose);
 node.Subscribe("/stats",stats);
 node.Subscribe("/odom",odom);
 auto velocity=node.Advertise<gz::msgs::Twist>("/cmd_vel");
 std::map<std::string,gz::transport::Node::Publisher> pubs;
 std::string line;
 while(std::getline(std::cin,line)) {try {
  auto in=json::parse(line); json out;
  const auto op=in.at("op").get<std::string>();
  if(op=="publish") {
   for(auto it=in.at("values").begin();it!=in.at("values").end();++it) {
    if(!pubs.count(it.key())) {pubs[it.key()]=node.Advertise<gz::msgs::Double>(it.key()); std::this_thread::sleep_for(150ms);}
    gz::msgs::Double d; d.set_data(it.value().get<double>()); pubs[it.key()].Publish(d);
   } out["ok"]=true;
  } else if(op=="velocity") {
   gz::msgs::Twist twist;twist.mutable_linear()->set_x(in.value("linear",0.0));twist.mutable_angular()->set_z(in.value("angular",0.0));
   out["ok"]=velocity.Publish(twist);
  } else if(op=="pose") {
   gz::msgs::Pose p; p.set_name(in.at("name")); auto xyz=in.at("xyz");
   p.mutable_position()->set_x(xyz[0]);p.mutable_position()->set_y(xyz[1]);p.mutable_position()->set_z(xyz[2]);p.mutable_orientation()->set_w(1);
   gz::msgs::Boolean response; bool result=false;
   out["ok"]=node.Request("/world/tangying_home/set_pose",p,5000,response,result)&&result&&response.data();
  } else if(op=="reset") {
   gz::msgs::WorldControl c;c.set_pause(true);c.mutable_reset()->set_all(true);
   gz::msgs::Boolean response;bool result=false;
   out["ok"]=node.Request("/world/tangying_home/control",c,5000,response,result)&&result&&response.data();
   std::this_thread::sleep_for(300ms);
   {std::lock_guard<std::mutex> lock(guard);images.clear();depths.clear();contacts.Clear();joints.Clear();odometry.Clear();oracle.Clear();iterations=0;statsSeen=false;}
  } else if(op=="step") {
   uint64_t target;
   {std::unique_lock<std::mutex> lock(guard);changed.wait_for(lock,5s,[]{return statsSeen;});target=iterations+in.value("steps",100);}
   gz::msgs::WorldControl c; c.set_pause(true); c.set_multi_step(in.value("steps",100));
   gz::msgs::Boolean response; bool result=false;
   out["ok"]=node.Request("/world/tangying_home/control",c,5000,response,result)&&result&&response.data();
   std::unique_lock<std::mutex> lock(guard);
   out["ok"]=out["ok"].get<bool>() && changed.wait_for(lock,20s,[&]{return iterations>=target;});
   out["iterations"]=iterations;
  } else if(op=="snapshot") {
   std::unique_lock<std::mutex> lock(guard); changed.wait_for(lock,3s,[]{return !images.empty() && !depths.empty();}); int64_t key=-1;
   for(auto it=images.rbegin();it!=images.rend();++it) if(depths.count(it->first)){key=it->first;break;}
   if(key<0) {out={{"ok",false},{"error","no synchronized RGB-D capture"},{"images",images.size()},{"depths",depths.size()},{"image_stamp",images.empty()?0:images.rbegin()->first},{"depth_stamp",depths.empty()?0:depths.rbegin()->first}};}
   else {
    std::string dir=in.at("path");std::filesystem::create_directories(dir);
    auto rgb=images[key];auto dep=depths[key];
    std::ofstream(dir+"/rgb.bin",std::ios::binary).write(rgb.data().data(),rgb.data().size());
    std::ofstream(dir+"/depth.bin",std::ios::binary).write(dep.data().data(),dep.data().size());
    rgb.clear_data();dep.clear_data(); write(dir+"/rgb.json",rgb);write(dir+"/depth.json",dep);
    write(dir+"/contact.json",contacts);write(dir+"/joints.json",joints);write(dir+"/oracle.json",oracle);
    write(dir+"/odom.json",odometry);
    out={{"ok",true},{"stamp_ns",key},{"width",rgb.width()},{"height",rgb.height()},{"path",dir}};
   }
  } else {out={{"ok",false},{"error","unknown operation"}};}
  std::cout<<out.dump()<<std::endl;
 }catch(const std::exception &e){std::cout<<json({{"ok",false},{"error",e.what()}}).dump()<<std::endl;}}
}
