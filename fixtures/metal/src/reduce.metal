#include <metal_stdlib>
using namespace metal;

// A deliberately production-shaped kernel (mirrors the patterns in real PyTorch MPS
// kernels: templated, simdgroup reduction, device/constant/threadgroup address spaces,
// thread-position attributes, pointer-heavy strided indexing) but self-contained so it
// needs no external headers. Used to guard that discovery handles real-world MSL without
// crashing and that pointer declarators never leak as arithmetic `*` mutants.
template <typename T>
kernel void strided_sum(device const T* in [[buffer(0)]],
                        device T* out [[buffer(1)]],
                        constant uint& row_stride [[buffer(2)]],
                        constant uint& n_cols [[buffer(3)]],
                        threadgroup T* scratch [[threadgroup(0)]],
                        uint gid [[thread_position_in_grid]],
                        uint lid [[thread_position_in_threadgroup]],
                        uint sg_lane [[thread_index_in_simdgroup]]) {
    const uint row = gid;
    device const T* row_ptr = in + row * row_stride;
    T acc = T(0);
    for (uint c = 0; c < n_cols; c += 1) {
        acc += row_ptr[c] * T(2) - T(1);
    }
    acc = simd_sum(acc);
    if (sg_lane == 0) {
        scratch[lid / 32] = acc;
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);
    if (lid == 0) {
        T total = T(0);
        for (uint s = 0; s < (n_cols + 31) / 32; s += 1) {
            total += scratch[s];
        }
        out[row] = total;
    }
}

template [[host_name("strided_sum_float")]] kernel void
strided_sum<float>(device const float*, device float*, constant uint&, constant uint&,
                   threadgroup float*, uint, uint, uint);
