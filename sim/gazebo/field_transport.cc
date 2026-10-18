// JSONL transport for the isolated Gazebo field-validation world.
// No pose/reset RPC exists: measurements are read from Gazebo sensor/controller topics.
#include <gz/transport/Node.hh>
#include <gz/msgs/boolean.pb.h>
#include <gz/msgs/contacts.pb.h>
#include <gz/msgs/imu.pb.h>
#include <gz/msgs/laserscan.pb.h>
#include <gz/msgs/stringmsg.pb.h>
#include <gz/msgs/twist.pb.h>
#include <gz/msgs/world_control.pb.h>
#include <gz/msgs/world_stats.pb.h>
#include <google/protobuf/util/json_util.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <functional>
#include <iostream>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

using json = nlohmann::json;
using namespace std::chrono_literals;
namespace {
std::mutex guard;
std::condition_variable changed;
std::map<std::string, json> states, scans, imus, contacts;
std::map<std::string, uint64_t> scanCounts, imuCounts, contactCounts;
json contactHistory = json::array();
uint64_t iterations = 0;
double simulationTime = 0;
bool statsSeen = false;

json jsonMessage(const google::protobuf::Message &msg) {
  std::string encoded;
  const auto result = google::protobuf::util::MessageToJsonString(msg, &encoded);
  if (!result.ok()) throw std::runtime_error("protobuf JSON serialization failed");
  return json::parse(encoded);
}

void stats(const gz::msgs::WorldStatistics &msg) {
  std::lock_guard<std::mutex> lock(guard);
  iterations = msg.iterations();
  simulationTime = msg.sim_time().sec() + 1e-9 * msg.sim_time().nsec();
  statsSeen = true; changed.notify_all();
}

void validateRobot(const std::string &id) {
  if (id != "ground-a" && id != "ground-b" && id != "uav-a")
    throw std::runtime_error("unknown robotId");
}

void connected(gz::transport::Node::Publisher &publisher) {
  for (int i = 0; i < 100 && !publisher.HasConnections(); ++i)
    std::this_thread::sleep_for(20ms);
  if (!publisher.HasConnections()) throw std::runtime_error("controller subscriber unavailable");
}
}

int main() {
  gz::transport::Node node;
  node.Subscribe("/world/field_validation/stats", stats);
  node.Subscribe("/stats", stats);
  std::map<std::string, gz::transport::Node::Publisher> velocities, stops;
  for (const std::string id : {"ground-a", "ground-b", "uav-a"}) {
    velocities[id] = node.Advertise<gz::msgs::Twist>("/field/" + id + "/cmd_vel");
    stops[id] = node.Advertise<gz::msgs::Boolean>("/field/" + id + "/stop");
    node.Subscribe<gz::msgs::StringMsg>("/field/" + id + "/state",
      std::function<void(const gz::msgs::StringMsg &)>([id](const auto &msg) {
        try {std::lock_guard<std::mutex> lock(guard); states[id] = json::parse(msg.data()); changed.notify_all();}
        catch (const std::exception &) {}
      }));
    node.Subscribe<gz::msgs::LaserScan>("/field/" + id + "/lidar",
      std::function<void(const gz::msgs::LaserScan &)>([id](const auto &msg) {
        std::lock_guard<std::mutex> lock(guard); scans[id] = jsonMessage(msg); ++scanCounts[id]; changed.notify_all();
      }));
    node.Subscribe<gz::msgs::IMU>("/field/" + id + "/imu",
      std::function<void(const gz::msgs::IMU &)>([id](const auto &msg) {
        std::lock_guard<std::mutex> lock(guard); imus[id] = jsonMessage(msg); ++imuCounts[id]; changed.notify_all();
      }));
    // Gazebo Harmonic Contact uses its scoped topic even when <topic> is set.
    node.Subscribe<gz::msgs::Contacts>("/world/field_validation/model/" + id +
      "/link/base_link/sensor/contact/contact",
      std::function<void(const gz::msgs::Contacts &)>([id](const auto &msg) {
        std::lock_guard<std::mutex> lock(guard); contacts[id] = jsonMessage(msg); ++contactCounts[id];
        const double contactTime = msg.header().stamp().sec() + 1e-9 * msg.header().stamp().nsec();
        for (const auto &contact : msg.contact()) {
          // Retain one record per contact pair per robot, including floor contacts.
          const auto key = id + ":" + contact.collision1().name() + ":" + contact.collision2().name();
          auto it = std::find_if(contactHistory.begin(), contactHistory.end(),
                                [&](const json &x) {return x.at("key") == key;});
          if (it == contactHistory.end()) {
            contactHistory.push_back({{"key", key}, {"robotId", id}, {"samples", 1},
              {"firstSimulationTime", contactTime}, {"lastSimulationTime", contactTime},
              {"contact", jsonMessage(contact)}});
          } else {(*it)["samples"] = it->at("samples").template get<uint64_t>() + 1;
                  (*it)["lastSimulationTime"] = contactTime;}
        }
        changed.notify_all();
      }));
  }
  std::string line;
  while (std::getline(std::cin, line)) {
    try {
      const auto input = json::parse(line);
      const std::string op = input.at("op");
      json output;
      if (op == "velocity") {
        const std::string id = input.at("robotId"); validateRobot(id);
        const auto velocity = input.value("velocity", json::array({0., 0., 0.}));
        if (!velocity.is_array() || velocity.size() != 3) throw std::runtime_error("velocity must be xyz");
        double xyz[3];
        for (int i=0; i<3; ++i) {xyz[i] = velocity.at(i).get<double>();
          if (!std::isfinite(xyz[i])) throw std::runtime_error("velocity must be finite");}
        const double yaw = input.value("yawRate", 0.);
        if (!std::isfinite(yaw)) throw std::runtime_error("yawRate must be finite");
        gz::msgs::Twist msg; msg.mutable_linear()->set_x(xyz[0]); msg.mutable_linear()->set_y(xyz[1]);
        msg.mutable_linear()->set_z(xyz[2]); msg.mutable_angular()->set_z(yaw);
        connected(velocities[id]); output = {{"ok", velocities[id].Publish(msg)}};
        // Give the asynchronous subscriber a chance before an immediately following step RPC.
        std::this_thread::sleep_for(10ms);
      } else if (op == "stop") {
        const std::string id = input.at("robotId"); validateRobot(id);
        gz::msgs::Boolean msg; msg.set_data(true); connected(stops[id]);
        output = {{"ok", stops[id].Publish(msg)}, {"physicalStopVerified", false}};
        std::this_thread::sleep_for(10ms);
      } else if (op == "step") {
        const int count = input.value("steps", 50);
        if (count < 1 || count > 10000) throw std::runtime_error("steps must be in [1, 10000]");
        uint64_t before;
        {std::unique_lock<std::mutex> lock(guard);
          if (!changed.wait_for(lock, 10s, []{return statsSeen;})) throw std::runtime_error("no Gazebo statistics");
          before = iterations;}
        gz::msgs::WorldControl command; command.set_pause(true); command.set_multi_step(count);
        gz::msgs::Boolean response; bool result = false;
        const bool requested = node.Request("/world/field_validation/control", command, 5000, response, result);
        if (!requested || !result || !response.data()) throw std::runtime_error("world step request failed");
        std::unique_lock<std::mutex> lock(guard);
        const bool completed = changed.wait_for(lock, 30s, [&]{return iterations >= before + count;});
        output = {{"ok", completed}, {"iterations", iterations}, {"requestedIterations", before + count},
                  {"simulationTime", simulationTime}};
        if (!completed) output["error"] = "world did not reach requested iterations";
      } else if (op == "snapshot") {
        std::unique_lock<std::mutex> lock(guard);
        if (input.value("waitForSensors", false))
          changed.wait_for(lock, 5s, []{return states.size() == 3 && scans.size() == 3 && imus.size() == 3;});
        output = {{"ok", states.size() == 3}, {"states", states}, {"iterations", iterations},
          {"simulationTime", simulationTime}, {"imus", imus}, {"contacts", contacts},
          {"contactHistory", contactHistory}, {"scanCounts", scanCounts}, {"imuCounts", imuCounts},
          {"contactCounts", contactCounts}};
        if (input.value("includeScans", true)) output["scans"] = scans;
      } else throw std::runtime_error("unsupported operation");
      std::cout << output.dump() << std::endl;
    } catch (const std::exception &error) {
      std::cout << json({{"ok", false}, {"error", error.what()}}).dump() << std::endl;
    }
  }
}
