# Metal mutation fixture

A self-contained Metal Shading Language (`.metal`) kernel plus a GPU host harness,
used by `tests/python/test_metal_support.py` to prove the full stryker-cxx Metal
loop: discover source-level mutants → recompile the `.metallib` → run the kernel on
the GPU → classify killed/survived.

Build and test commands (run from this directory):

```bash
mkdir -p build \
  && xcrun metal -o build/kernels.metallib src/add.metal \
  && swiftc -O host/main.swift -o build/hosttest   # build-command
./build/hosttest                                     # test-command
```

Run mutation testing over it:

```bash
stryker-cxx run \
  --repo . \
  --files src/add.metal \
  --include-metal \
  --mutation-level Complete \
  --build-command "mkdir -p build && xcrun metal -o build/kernels.metallib src/add.metal && swiftc -O host/main.swift -o build/hosttest" \
  --test-command "./build/hosttest" \
  --report mutation.json
```

Expected: the `thread_position_in_grid` swap and the `+` mutation are **killed**;
`device*`→`constant*` on the writable `out` buffer is a **build error**; the same swap
on the read-only `a`/`b` buffers **survives** (equivalent). Requires macOS with the
Metal toolchain (`xcrun metal`) and a usable Metal GPU.
