#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#include <doctest/doctest.h>

int value(int input) {
  return input == 1 ? 2 : 0;
}

TEST_CASE("math equality") {
  CHECK(value(1) == 2);
}
