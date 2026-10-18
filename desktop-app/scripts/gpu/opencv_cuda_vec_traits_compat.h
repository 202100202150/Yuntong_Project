#pragma once

// NVCC with the MSVC host compiler does not implement GCC's include_next.
// Pre-include this wrapper instead: it includes the pinned OpenCV header via
// the normal search path and then supplies the missing CUDA 12.9 64-bit traits.
#include <opencv2/cudev/util/vec_traits.hpp>

namespace cv {
namespace cudev {

#define YUNTONG_CUDEV_MAKE_VEC_64(scalar_type, vector_prefix)                 \
    template<> struct MakeVec<scalar_type, 1> { typedef scalar_type type; };   \
    template<> struct MakeVec<scalar_type, 2> { typedef vector_prefix##2 type; }; \
    template<> struct MakeVec<scalar_type, 3> { typedef vector_prefix##3 type; }; \
    template<> struct MakeVec<scalar_type, 4> { typedef vector_prefix##4 type; };

YUNTONG_CUDEV_MAKE_VEC_64(long long, longlong)
YUNTONG_CUDEV_MAKE_VEC_64(unsigned long long, ulonglong)

#define YUNTONG_CUDEV_VEC_TRAITS_64(scalar_type, vector_prefix, make_prefix)     \
    template<> struct VecTraits<scalar_type>                                      \
    {                                                                             \
        typedef scalar_type elem_type;                                           \
        enum {cn=1};                                                             \
        __host__ __device__ __forceinline__ static scalar_type all(scalar_type v) { return v; } \
        __host__ __device__ __forceinline__ static scalar_type make(scalar_type v) { return v; } \
        __host__ __device__ __forceinline__ static scalar_type make(const scalar_type* v) { return *v; } \
    };                                                                            \
    template<> struct VecTraits<vector_prefix##1>                                \
    {                                                                             \
        typedef scalar_type elem_type;                                           \
        enum {cn=1};                                                             \
        __host__ __device__ __forceinline__ static vector_prefix##1 all(scalar_type v) { return make_prefix##1(v); } \
        __host__ __device__ __forceinline__ static vector_prefix##1 make(scalar_type v) { return make_prefix##1(v); } \
        __host__ __device__ __forceinline__ static vector_prefix##1 make(const scalar_type* v) { return make_prefix##1(*v); } \
    };                                                                            \
    template<> struct VecTraits<vector_prefix##2>                                \
    {                                                                             \
        typedef scalar_type elem_type;                                           \
        enum {cn=2};                                                             \
        __host__ __device__ __forceinline__ static vector_prefix##2 all(scalar_type v) { return make_prefix##2(v, v); } \
        __host__ __device__ __forceinline__ static vector_prefix##2 make(scalar_type x, scalar_type y) { return make_prefix##2(x, y); } \
        __host__ __device__ __forceinline__ static vector_prefix##2 make(const scalar_type* v) { return make_prefix##2(v[0], v[1]); } \
    };                                                                            \
    template<> struct VecTraits<vector_prefix##3>                                \
    {                                                                             \
        typedef scalar_type elem_type;                                           \
        enum {cn=3};                                                             \
        __host__ __device__ __forceinline__ static vector_prefix##3 all(scalar_type v) { return make_prefix##3(v, v, v); } \
        __host__ __device__ __forceinline__ static vector_prefix##3 make(scalar_type x, scalar_type y, scalar_type z) { return make_prefix##3(x, y, z); } \
        __host__ __device__ __forceinline__ static vector_prefix##3 make(const scalar_type* v) { return make_prefix##3(v[0], v[1], v[2]); } \
    };                                                                            \
    template<> struct VecTraits<vector_prefix##4>                                \
    {                                                                             \
        typedef scalar_type elem_type;                                           \
        enum {cn=4};                                                             \
        __host__ __device__ __forceinline__ static vector_prefix##4 all(scalar_type v) { return make_prefix##4(v, v, v, v); } \
        __host__ __device__ __forceinline__ static vector_prefix##4 make(scalar_type x, scalar_type y, scalar_type z, scalar_type w) { return make_prefix##4(x, y, z, w); } \
        __host__ __device__ __forceinline__ static vector_prefix##4 make(const scalar_type* v) { return make_prefix##4(v[0], v[1], v[2], v[3]); } \
    };

YUNTONG_CUDEV_VEC_TRAITS_64(long long, longlong, make_longlong)
YUNTONG_CUDEV_VEC_TRAITS_64(unsigned long long, ulonglong, make_ulonglong)

#undef YUNTONG_CUDEV_VEC_TRAITS_64
#undef YUNTONG_CUDEV_MAKE_VEC_64

} // namespace cudev
} // namespace cv
