#include <cstdlib>

int value(int input) {
  if (input == 1) {
    return 2;
  }
  return 0;
}

int main() {
  return value(1) == 2 ? EXIT_SUCCESS : EXIT_FAILURE;
}
