#include <gtest/gtest.h>

int value(int input) {
  return input == 1 ? 2 : 0;
}

TEST(MathFixture, Equality) {
  EXPECT_EQ(value(1), 2);
}
