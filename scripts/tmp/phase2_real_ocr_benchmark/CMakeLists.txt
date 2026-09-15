cmake_minimum_required(VERSION 3.25)

project(phase2_real_ocr_benchmark LANGUAGES CXX)

if(NOT WIN32 OR NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "phase2_real_ocr_benchmark requires Windows x64 with MSVC")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

get_filename_component(REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
set(WINDOWS_SDK_ROOT "C:/Program Files (x86)/Windows Kits/10")
set(CPPWINRT_INCLUDE
    "${WINDOWS_SDK_ROOT}/Include/${CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION}/cppwinrt")
if(NOT EXISTS "${CPPWINRT_INCLUDE}/winrt/Windows.Media.Ocr.h")
  message(FATAL_ERROR "C++/WinRT projection not found: ${CPPWINRT_INCLUDE}")
endif()

add_executable(
  phase2_real_ocr_benchmark
  benchmark.cpp
  "${REPO_ROOT}/src/common/contracts.cpp"
  "${REPO_ROOT}/src/detector/roi.cpp"
  "${REPO_ROOT}/src/knowledge/augment_catalog.cpp"
  "${REPO_ROOT}/src/replay/wic_image_codec.cpp"
  "${REPO_ROOT}/src/vision/ocr.cpp"
  "${REPO_ROOT}/src/vision/software_bitmap.cpp"
  "${REPO_ROOT}/src/vision/text_matcher.cpp"
  "${REPO_ROOT}/src/vision/title_preprocessor.cpp"
)

target_include_directories(
  phase2_real_ocr_benchmark
  PRIVATE
    "${REPO_ROOT}/include"
    "${CPPWINRT_INCLUDE}"
)

target_compile_definitions(
  phase2_real_ocr_benchmark
  PRIVATE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
    UNICODE
    _UNICODE
    _WIN32_WINNT=0x0A00
    WINVER=0x0A00
)

target_compile_options(
  phase2_real_ocr_benchmark
  PRIVATE /W4 /WX /permissive- /EHsc /utf-8 /Zc:__cplusplus
)

target_link_libraries(
  phase2_real_ocr_benchmark
  PRIVATE Normaliz ole32 runtimeobject windowsapp windowscodecs
)

set_target_properties(
  phase2_real_ocr_benchmark
  PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)
