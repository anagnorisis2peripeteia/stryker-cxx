cmake_minimum_required(VERSION 3.16)
project(stryker_cxx_cmake_fixture LANGUAGES CXX)

enable_testing()

add_executable(math_test math_test.cpp)
target_compile_features(math_test PRIVATE cxx_std_17)

add_test(NAME math COMMAND math_test)
