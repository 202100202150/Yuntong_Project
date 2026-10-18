#pragma once

// CUDA 12.9's Windows runtime headers no longer expose the historical
// ``ulong`` alias that OpenCV's CUDA contrib headers still reference.  This
// header is pre-included only for NVCC compilation of the pinned OpenCV build;
// it does not change the public Python API or the TensorRT detector.
#if defined(__CUDACC__) && defined(_WIN32)
typedef unsigned long ulong;
#endif
