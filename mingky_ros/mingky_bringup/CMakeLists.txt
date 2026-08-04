cmake_minimum_required(VERSION 3.8)
project(mingky_bringup)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY launch config
  DESTINATION share/${PROJECT_NAME}
)

install(
  PROGRAMS
    scripts/run_waypoint_teleop.sh
    scripts/capture_waypoint.sh
    scripts/check_waypoints.py
  DESTINATION lib/${PROJECT_NAME}
)

install(
  FILES README.md
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
