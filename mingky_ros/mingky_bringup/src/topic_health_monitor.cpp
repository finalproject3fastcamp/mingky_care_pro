#include <chrono>
#include <deque>
#include <functional>
#include <memory>
#include <string>
#include <unordered_map>

#include "diagnostic_msgs/msg/diagnostic_array.hpp"
#include "diagnostic_msgs/msg/diagnostic_status.hpp"
#include "diagnostic_msgs/msg/key_value.hpp"
#include "geometry_msgs/msg/pose_with_covariance_stamped.hpp"
#include "geometry_msgs/msg/twist.hpp"
#include "nav_msgs/msg/odometry.hpp"
#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/laser_scan.hpp"

namespace
{
using SteadyClock = std::chrono::steady_clock;

struct TopicSample
{
  SteadyClock::time_point started{SteadyClock::now()};
  std::deque<SteadyClock::time_point> received;
};

class TopicHealthMonitor : public rclcpp::Node
{
public:
  TopicHealthMonitor()
  : Node("topic_health_monitor")
  {
    publisher_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>(
      "/mingky/topic_health", rclcpp::QoS(1));
    const auto qos = rclcpp::SensorDataQoS().keep_last(1);
    scan_subscription_ = create_subscription<sensor_msgs::msg::LaserScan>(
      "/scan", qos,
      [this](sensor_msgs::msg::LaserScan::ConstSharedPtr) {record("/scan");});
    odom_subscription_ = create_subscription<nav_msgs::msg::Odometry>(
      "/odom", qos,
      [this](nav_msgs::msg::Odometry::ConstSharedPtr) {record("/odom");});
    pose_subscription_ =
      create_subscription<geometry_msgs::msg::PoseWithCovarianceStamped>(
      "/amcl_pose", qos,
      [this](geometry_msgs::msg::PoseWithCovarianceStamped::ConstSharedPtr) {
        record("/amcl_pose");
      });
    command_subscription_ = create_subscription<geometry_msgs::msg::Twist>(
      "/cmd_vel", qos,
      [this](geometry_msgs::msg::Twist::ConstSharedPtr) {record("/cmd_vel");});
    timer_ = create_wall_timer(
      std::chrono::seconds(1), std::bind(&TopicHealthMonitor::publish, this));
  }

private:
  static constexpr auto window_ = std::chrono::seconds(5);
  static constexpr std::size_t max_samples_ = 256;

  void record(const std::string & topic)
  {
    auto & sample = samples_[topic];
    sample.received.push_back(SteadyClock::now());
    while (sample.received.size() > max_samples_) {
      sample.received.pop_front();
    }
  }

  void publish()
  {
    const auto now = SteadyClock::now();
    diagnostic_msgs::msg::DiagnosticArray output;
    output.header.stamp = get_clock()->now();
    for (const auto * topic : {"/scan", "/odom", "/amcl_pose", "/cmd_vel"}) {
      auto & sample = samples_[topic];
      while (!sample.received.empty() && now - sample.received.front() > window_) {
        sample.received.pop_front();
      }

      diagnostic_msgs::msg::DiagnosticStatus status;
      status.level = diagnostic_msgs::msg::DiagnosticStatus::OK;
      status.name = topic;
      status.hardware_id = "pinky";
      status.message = "topic timing sample";
      const auto last = sample.received.empty() ? sample.started : sample.received.back();
      const auto age = std::chrono::duration<double>(now - last).count();
      diagnostic_msgs::msg::KeyValue age_value;
      age_value.key = "age_sec";
      age_value.value = std::to_string(age);
      status.values.push_back(std::move(age_value));
      if (sample.received.size() >= 2) {
        const auto span = std::chrono::duration<double>(
          sample.received.back() - sample.received.front()).count();
        if (span > 0.0) {
          const auto hz = static_cast<double>(sample.received.size() - 1) / span;
          diagnostic_msgs::msg::KeyValue hz_value;
          hz_value.key = "hz";
          hz_value.value = std::to_string(hz);
          status.values.push_back(std::move(hz_value));
        }
      }
      output.status.push_back(std::move(status));
    }
    publisher_->publish(output);
  }

  std::unordered_map<std::string, TopicSample> samples_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr publisher_;
  rclcpp::Subscription<sensor_msgs::msg::LaserScan>::SharedPtr scan_subscription_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr odom_subscription_;
  rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr pose_subscription_;
  rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr command_subscription_;
  rclcpp::TimerBase::SharedPtr timer_;
};
}  // namespace

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<TopicHealthMonitor>());
  rclcpp::shutdown();
  return 0;
}
