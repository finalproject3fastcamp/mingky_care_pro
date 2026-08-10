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
endif()

ament_package()
