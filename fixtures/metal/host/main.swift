// GPU host harness for the add_arrays kernel. Loads build/kernels.metallib, runs the
// kernel over 1024 elements and asserts the result. Exit 0 = pass (mutant survives),
// non-zero = fail (mutant killed). Used by the stryker-cxx Metal end-to-end test.
import Metal
import Foundation

let n = 1024
guard let dev = MTLCreateSystemDefaultDevice() else {
    FileHandle.standardError.write("no Metal device\n".data(using: .utf8)!)
    exit(2)
}
let q = dev.makeCommandQueue()!
let lib = try! dev.makeLibrary(URL: URL(fileURLWithPath: "build/kernels.metallib"))
let fn = lib.makeFunction(name: "add_arrays")!
let pso = try! dev.makeComputePipelineState(function: fn)

var a = [Float](repeating: 0, count: n)
var b = [Float](repeating: 0, count: n)
for i in 0..<n { a[i] = Float(i); b[i] = Float(2 * i) }
let ba = dev.makeBuffer(bytes: &a, length: n * 4, options: .storageModeShared)!
let bb = dev.makeBuffer(bytes: &b, length: n * 4, options: .storageModeShared)!
let bo = dev.makeBuffer(length: n * 4, options: .storageModeShared)!

let cb = q.makeCommandBuffer()!
let enc = cb.makeComputeCommandEncoder()!
enc.setComputePipelineState(pso)
enc.setBuffer(ba, offset: 0, index: 0)
enc.setBuffer(bb, offset: 0, index: 1)
enc.setBuffer(bo, offset: 0, index: 2)
enc.dispatchThreads(MTLSize(width: n, height: 1, depth: 1),
                    threadsPerThreadgroup: MTLSize(width: 64, height: 1, depth: 1))
enc.endEncoding()
cb.commit()
cb.waitUntilCompleted()

let out = bo.contents().bindMemory(to: Float.self, capacity: n)
for i in 0..<n {
    let expect = Float(i) + Float(2 * i)
    if abs(out[i] - expect) > 1e-5 {
        FileHandle.standardError.write("FAIL at \(i): got \(out[i]) expected \(expect)\n".data(using: .utf8)!)
        exit(1)
    }
}
print("PASS: add_arrays correct over \(n) elements")
exit(0)
