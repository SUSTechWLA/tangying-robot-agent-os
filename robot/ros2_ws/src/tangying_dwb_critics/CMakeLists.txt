cmake_minimum_required(VERSION 3.8)
project(tangying_dwb_critics)

find_package(ament_cmake REQUIRED)
find_package(dwb_core REQUIRED)
find_package(dwb_critics REQUIRED)
find_package(nav2_util REQUIRED)
find_package(pluginlib REQUIRED)

add_library(continuous_goal SHARED src/continuous_goal.cpp src/approach_tracking.cpp)
target_compile_features(continuous_goal PUBLIC cxx_std_17)
target_include_directories(continuous_goal PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
ament_target_dependencies(continuous_goal dwb_core dwb_critics nav2_util pluginlib)
pluginlib_export_plugin_description_file(dwb_core critics.xml)
install(TARGETS continuous_goal EXPORT export_continuous_goal
  ARCHIVE DESTINATION lib LIBRARY DESTINATION lib RUNTIME DESTINATION bin)
install(DIRECTORY include/ DESTINATION include)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  find_package(dwb_plugins REQUIRED)
  ament_add_gtest(test_continuous_goal test/test_continuous_goal.cpp)
  target_link_libraries(test_continuous_goal continuous_goal)
  ament_target_dependencies(test_continuous_goal dwb_core dwb_plugins pluginlib)
  ament_add_gtest(test_approach_tracking test/test_approach_tracking.cpp)
  target_link_libraries(test_approach_tracking continuous_goal)
  ament_target_dependencies(test_approach_tracking dwb_core dwb_critics dwb_plugins pluginlib)
endif()

ament_export_targets(export_continuous_goal HAS_LIBRARY_TARGET)
ament_export_dependencies(dwb_core dwb_critics nav2_util pluginlib)
ament_package()
