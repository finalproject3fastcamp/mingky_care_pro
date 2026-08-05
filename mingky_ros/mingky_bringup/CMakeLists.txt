cmake_minimum_required(VERSION 3.8)
project(mingky_bringup)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY launch config map
  DESTINATION share/${PROJECT_NAME}
)

install(
  PROGRAMS
    scripts/run_waypoint_teleop.sh
    scripts/capture_waypoint.sh
    scripts/run_nav2_manual_test.sh
    scripts/run_nav2_waypoint_test.sh
    scripts/check_waypoints.py
  DESTINATION lib/${PROJECT_NAME}
)

install(
  FILES README.md
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
