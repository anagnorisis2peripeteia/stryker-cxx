// Benchmark fixture: a spread of mutable C++ constructs so the mutant counts exercise many
// operators (arithmetic, relational, logical, bitwise, unary, literals, calls, control flow).
#include <cstdlib>
#include <vector>
#include <string>

int classify(int n) {
  int score = 0;
  for (int i = 1; i <= n; ++i) {
    if (i % 2 == 0 && i > 3) {
      score += i * 2 - 1;
    } else if (i < 0 || i == 7) {
      score -= i / 2;
    } else {
      score = score << 1;
    }
  }
  return score > 100 ? 100 : score;
}

double blend(double a, double b) {
  double w = 0.5;
  return a * w + b * (1.0 - w);
}

bool contains(const std::vector<int>& xs, int target) {
  for (std::size_t i = 0; i < xs.size(); ++i) {
    if (xs[i] == target) {
      return true;
    }
  }
  return false;
}

std::string label(int n) {
  if (n >= 100) {
    return "high";
  }
  return n <= 0 ? "none" : "some";
}

int main() {
  std::vector<int> xs = {2, 4, 7, 9};
  int ok = 0;
  ok += classify(10) == 100 ? 1 : 0;
  ok += (blend(2.0, 4.0) == 3.0) ? 1 : 0;
  ok += contains(xs, 7) ? 1 : 0;
  ok += (label(50) == "some") ? 1 : 0;
  return ok == 4 ? EXIT_SUCCESS : EXIT_FAILURE;
}
