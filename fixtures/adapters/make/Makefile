CXX ?= c++
CXXFLAGS ?= -std=c++17 -Wall -Wextra

.PHONY: all test clean

all: math_test

math_test: math_test.cpp
	$(CXX) $(CXXFLAGS) $< -o $@

test: math_test
	./math_test

clean:
	rm -f math_test
