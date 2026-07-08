#include <metal_stdlib>
using namespace metal;

// Minimal elementwise kernel used to exercise stryker-cxx Metal mutation support.
// Mutating the address space of `out` (device->constant) must fail to compile; the
// thread-position attribute and the `+` are behaviour-carrying and must be killed by
// the GPU host test. The `*`/`&` in pointer declarators must NOT be mutated.
kernel void add_arrays(device const float* a,
                       device const float* b,
                       device float* out,
                       uint gid [[thread_position_in_grid]]) {
    out[gid] = a[gid] + b[gid];
}
