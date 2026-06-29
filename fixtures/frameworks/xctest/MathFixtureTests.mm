#import <XCTest/XCTest.h>

static int value(int input) {
  return input == 1 ? 2 : 0;
}

@interface MathFixtureTests : XCTestCase
@end

@implementation MathFixtureTests
- (void)testEquality {
  XCTAssertEqual(value(1), 2);
}
@end
