cmake_minimum_required(VERSION 3.8)
project(mingky_bringup)

find_package(ament_cmake REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)

add_executable(topic_health_monitor src/topic_health_monitor.cpp)
ament_target_dependencies(
  topic_health_monitor
  diagnostic_msgs geometry_msgs nav_msgs rclcpp sensor_msgs
)

install(TARGETS topic_health_monitor DESTINATION lib/${PROJECT_NAME})

install(
  DIRECTORY launch config map
  DESTINATION share/${PROJECT_NAME}
)

install(
  PROGRAMS
    scripts/run_waypoint_teleop.sh
    scripts/capture_waypoint.sh
    scripts/battery_status.py
    scripts/install_battery_command.sh
    scripts/run_nav2_manual_test.sh
    scripts/run_nav2_waypoint_test.sh
    scripts/start_guidance_test.sh
    scripts/check_waypoints.py
    scripts/run_discovery_server.sh
  DESTINATION lib/${PROJECT_NAME}
)

# use_discovery_server.sh 는 source 로 써야 해서 실행 파일이 아니라
# share 에 둔다. ros2 run 으로는 환경 변수를 부르는 셸에 남길 수 없다.
install(
  FILES scripts/use_discovery_server.sh
  DESTINATION share/${PROJECT_NAME}/scripts
)

install(
  FILES README.md
  DESTINATION share/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_system_launch test/test_system_launch.py)
  ament_add_pytest_test(test_waypoint_config test/test_waypoint_config.py)
  ament_add_pytest_test(test_battery_command test/test_battery_command.py)
  ament_add_pytest_test(test_nav2_progress_config test/test_nav2_progress_config.py)
endif()

ament_package()
