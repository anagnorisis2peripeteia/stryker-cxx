#include <catch2/catch_test_macros.hpp>

int value(int input) {
  return input == 1 ? 2 : 0;
}

TEST_CASE("math equality", "[math]") {
  REQUIRE(value(1) == 2);
}
