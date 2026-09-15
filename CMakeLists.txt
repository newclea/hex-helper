cmake_minimum_required(VERSION 3.25)

project(lol_augment_assistant VERSION 0.2.0 LANGUAGES CXX)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_BINARY_DIR)
  message(FATAL_ERROR "In-source builds are disabled. Use scripts/build.ps1.")
endif()

if(NOT WIN32 OR NOT MSVC)
  message(FATAL_ERROR "Wave2 Foundation Phase 1 supports 64-bit Windows with MSVC only.")
endif()

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "A 64-bit (x64) generator is required.")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(LOL_ASSISTANT_ARTIFACT_ROOT
    "${CMAKE_BINARY_DIR}/artifacts"
    CACHE PATH "Root for integration-test artifacts")
file(MAKE_DIRECTORY "${LOL_ASSISTANT_ARTIFACT_ROOT}")

if(NOT CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION)
  message(FATAL_ERROR "CMake did not select a Windows SDK version.")
endif()
set(LOL_ASSISTANT_WINDOWS_SDK_ROOT "C:/Program Files (x86)/Windows Kits/10"
    CACHE PATH "Windows 10 SDK root")
set(LOL_ASSISTANT_CPPWINRT_INCLUDE
    "${LOL_ASSISTANT_WINDOWS_SDK_ROOT}/Include/${CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION}/cppwinrt")
if(NOT EXISTS
   "${LOL_ASSISTANT_CPPWINRT_INCLUDE}/winrt/Windows.Graphics.Capture.h")
  message(FATAL_ERROR
    "C++/WinRT projection not found under the selected Windows SDK: "
    "${LOL_ASSISTANT_CPPWINRT_INCLUDE}")
endif()

set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

foreach(config Debug Release RelWithDebInfo MinSizeRel)
  string(TOUPPER "${config}" config_upper)
  set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY_${config_upper} "${CMAKE_BINARY_DIR}/lib")
  set(CMAKE_LIBRARY_OUTPUT_DIRECTORY_${config_upper} "${CMAKE_BINARY_DIR}/bin")
  set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_${config_upper} "${CMAKE_BINARY_DIR}/bin")
endforeach()

function(lol_assistant_configure_target target_name)
  target_compile_features(${target_name} PUBLIC cxx_std_20)
  target_compile_definitions(
    ${target_name}
    PUBLIC
      NOMINMAX
      WIN32_LEAN_AND_MEAN
      UNICODE
      _UNICODE
      _WIN32_WINNT=0x0A00
      WINVER=0x0A00
  )
  target_compile_options(
    ${target_name}
    PRIVATE
      /W4
      /WX
      /permissive-
      /EHsc
      /utf-8
      /Zc:__cplusplus
  )
endfunction()

add_library(
  lol_assistant_core
  STATIC
    src/app/augment_frame_processor.cpp
    src/app/health_json.cpp
    src/app/live_capture_supervisor.cpp
    src/app/live_control.cpp
    src/app/mayhem_selection_scheduler.cpp
    src/app/session_runtime.cpp
    src/collection/sample_collector.cpp
    src/common/contracts.cpp
    src/capture/window_info.cpp
    src/capture/desktop_capture_geometry.cpp
    src/capture/desktop_duplication_source.cpp
    src/capture/dxgi_desktop_duplication_backend.cpp
    src/capture/windows_graphics_capture_source.cpp
    src/detector/augment_screen_detector.cpp
    src/detector/roi.cpp
    src/knowledge/augment_catalog.cpp
    src/live_client/live_client_data.cpp
    src/lcu/lcu_context.cpp
    src/output/debug_artifact_writer.cpp
    src/output/debug_preview_window.cpp
    src/output/structured_json_writer.cpp
    src/replay/image_replay_source.cpp
    src/replay/wic_image_codec.cpp
    src/state/phase1_game_state_reducer.cpp
    src/storage/async_storage_writer.cpp
    src/storage/jsonl_writer.cpp
    src/storage/session_store.cpp
    src/vision/hover_click_selector.cpp
    src/vision/icon_matcher.cpp
    src/vision/ocr.cpp
    src/vision/recognition_pipeline.cpp
    src/vision/selected_card_detector.cpp
    src/vision/software_bitmap.cpp
    src/vision/text_matcher.cpp
    src/vision/title_preprocessor.cpp
)

target_include_directories(
  lol_assistant_core
  PUBLIC
    "${LOL_ASSISTANT_CPPWINRT_INCLUDE}"
    "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
    "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/src/app>"
  PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src/storage"
)

target_link_libraries(
  lol_assistant_core
  PUBLIC
    Normaliz
    bcrypt
    d3d11
    dxgi
    dwmapi
    gdi32
    iphlpapi
    ole32
    runtimeobject
    shcore
    user32
    windowsapp
    windowscodecs
    winhttp
    ws2_32
    winsqlite3
)

lol_assistant_configure_target(lol_assistant_core)

add_library(lol_assistant_app_cli STATIC src/app/cli.cpp)
target_include_directories(
  lol_assistant_app_cli
  PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/src/app"
)
lol_assistant_configure_target(lol_assistant_app_cli)

add_executable(lol_augment_assistant src/app/main.cpp)
target_link_libraries(
  lol_augment_assistant
  PRIVATE lol_assistant_app_cli lol_assistant_core
)
target_compile_definitions(
  lol_augment_assistant
  PRIVATE
    LOL_ASSISTANT_VERSION="${PROJECT_VERSION}"
)
lol_assistant_configure_target(lol_augment_assistant)

include(CTest)
if(BUILD_TESTING)
  function(lol_assistant_add_cpp_test test_name source_file)
    add_executable(${test_name} ${source_file})
    target_link_libraries(${test_name} PRIVATE lol_assistant_core)
    lol_assistant_configure_target(${test_name})
    add_test(NAME ${test_name} COMMAND ${test_name} ${ARGN})
    set_tests_properties(${test_name} PROPERTIES
      WORKING_DIRECTORY "${LOL_ASSISTANT_ARTIFACT_ROOT}")
  endfunction()

  lol_assistant_add_cpp_test(common_contracts_test tests/common/common_contracts_test.cpp)
  lol_assistant_add_cpp_test(capture_minimal_test tests/capture/capture_minimal_test.cpp)
  lol_assistant_add_cpp_test(desktop_capture_geometry_test
    tests/capture/desktop_capture_geometry_test.cpp)
  lol_assistant_add_cpp_test(desktop_duplication_test
    tests/capture/desktop_duplication_test.cpp)
  target_include_directories(desktop_duplication_test PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src/capture")
  lol_assistant_add_cpp_test(detector_test tests/detector/detector_test.cpp)
  lol_assistant_add_cpp_test(sample_collector_test
    tests/collection/sample_collector_test.cpp)
  target_compile_definitions(sample_collector_test PRIVATE
    LOL_COLLECTION_TEST_ROOT=L"${LOL_ASSISTANT_ARTIFACT_ROOT}/sample_collector")
  lol_assistant_add_cpp_test(hover_click_selector_test
    tests/vision/hover_click_selector_test.cpp)
  lol_assistant_add_cpp_test(text_matcher_test tests/vision/text_matcher_test.cpp)
  lol_assistant_add_cpp_test(icon_matcher_test
    tests/vision/icon_matcher_test.cpp)
  target_compile_definitions(icon_matcher_test PRIVATE
    LOL_ASSISTANT_ICON_TEMPLATE_ROOT=L"${CMAKE_CURRENT_SOURCE_DIR}/data/knowledge/augment_icons")
  lol_assistant_add_cpp_test(title_preprocessor_test
    tests/vision/title_preprocessor_test.cpp)
  lol_assistant_add_cpp_test(recognition_pipeline_test tests/vision/recognition_pipeline_test.cpp)
  lol_assistant_add_cpp_test(selected_card_detector_test
    tests/vision/selected_card_detector_test.cpp)
  target_include_directories(selected_card_detector_test PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src/vision")
  target_compile_definitions(selected_card_detector_test PRIVATE
    LOL_SELECTED_CARD_REAL_DATA_ROOT=L"${CMAKE_CURRENT_SOURCE_DIR}/data/dataset/augment_offers/real"
    LOL_SELECTED_CARD_HUD_AUDIT_ROOT=L"${CMAKE_CURRENT_SOURCE_DIR}/outputs/tmp/hud_selection_audit")
  lol_assistant_add_cpp_test(ocr_smoke_test tests/vision/ocr_smoke_test.cpp)
  set_tests_properties(ocr_smoke_test PROPERTIES SKIP_RETURN_CODE 77)

  lol_assistant_add_cpp_test(augment_catalog_test tests/knowledge/augment_catalog_test.cpp)
  target_compile_definitions(augment_catalog_test PRIVATE
    LOL_ASSISTANT_CATALOG_PATH="${CMAKE_CURRENT_SOURCE_DIR}/data/knowledge/augments.zh-CN.json")

  lol_assistant_add_cpp_test(state_worker_tests tests/state/phase1_game_state_reducer_test.cpp)
  lol_assistant_add_cpp_test(storage_worker_tests tests/storage/storage_test.cpp
    "${LOL_ASSISTANT_ARTIFACT_ROOT}"
    "${LOL_ASSISTANT_ARTIFACT_ROOT}/storage_runtime")

  lol_assistant_add_cpp_test(replay_tests tests/replay/image_replay_source_test.cpp)
  target_compile_definitions(replay_tests PRIVATE
    LOL_TEST_OUTPUT_ROOT=L"${LOL_ASSISTANT_ARTIFACT_ROOT}")

  lol_assistant_add_cpp_test(output_tests tests/output/output_test.cpp)
  target_compile_definitions(output_tests PRIVATE
    LOL_TEST_OUTPUT_ROOT=L"${LOL_ASSISTANT_ARTIFACT_ROOT}")

  lol_assistant_add_cpp_test(cli_test tests/integration/cli_test.cpp)
  target_link_libraries(cli_test PRIVATE lol_assistant_app_cli)
  target_compile_definitions(cli_test PRIVATE
    LOL_ASSISTANT_SOURCE_ROOT=L"${CMAKE_CURRENT_SOURCE_DIR}")

  lol_assistant_add_cpp_test(live_control_test
    tests/integration/live_control_test.cpp)
  lol_assistant_add_cpp_test(health_json_test
    tests/integration/health_json_test.cpp)
  lol_assistant_add_cpp_test(live_client_data_test
    tests/live_client/live_client_data_test.cpp)
  lol_assistant_add_cpp_test(mayhem_selection_scheduler_test
    tests/live_client/mayhem_selection_scheduler_test.cpp)
  lol_assistant_add_cpp_test(lcu_context_test
    tests/lcu/lcu_context_test.cpp)

  lol_assistant_add_cpp_test(phase5_live_integration_test
    tests/integration/phase5_live_integration_test.cpp)
  target_link_libraries(phase5_live_integration_test PRIVATE
    lol_assistant_app_cli)
  target_compile_definitions(phase5_live_integration_test PRIVATE
    LOL_ASSISTANT_SOURCE_ROOT=L"${CMAKE_CURRENT_SOURCE_DIR}")

  add_executable(runtime_paths_test
    tests/integration/runtime_paths_test.cpp)
  lol_assistant_configure_target(runtime_paths_test)
  add_dependencies(runtime_paths_test lol_augment_assistant)
  add_test(
    NAME runtime_paths_test
    COMMAND runtime_paths_test
            "$<TARGET_FILE:lol_augment_assistant>"
            "${CMAKE_CURRENT_SOURCE_DIR}/data/knowledge/augments.zh-CN.json"
            "${CMAKE_CURRENT_SOURCE_DIR}/data/knowledge/augment_icons/manifest.json")
  set_tests_properties(runtime_paths_test PROPERTIES
    WORKING_DIRECTORY "${LOL_ASSISTANT_ARTIFACT_ROOT}")

  lol_assistant_add_cpp_test(session_runtime_test
    tests/integration/session_runtime_test.cpp)
  target_compile_definitions(session_runtime_test PRIVATE
    LOL_SESSION_ADAPTER_TMP_ROOT=L"${CMAKE_CURRENT_SOURCE_DIR}/outputs/tmp/session_adapter"
    LOL_SESSION_RUNTIME_ROOT=L"${CMAKE_CURRENT_SOURCE_DIR}/outputs/runtime"
    LOL_ASSISTANT_CATALOG_PATH=L"${CMAKE_CURRENT_SOURCE_DIR}/data/knowledge/augments.zh-CN.json")

  lol_assistant_add_cpp_test(augment_frame_processor_test
    tests/integration/augment_frame_processor_test.cpp)
  target_compile_definitions(augment_frame_processor_test PRIVATE
    LOL_FRAME_PROCESSOR_TMP_ROOT=L"${CMAKE_CURRENT_SOURCE_DIR}/outputs/tmp/frame_processor"
    LOL_FRAME_PROCESSOR_RUNTIME_ROOT=L"${CMAKE_CURRENT_SOURCE_DIR}/outputs/runtime"
    LOL_ASSISTANT_CATALOG_PATH=L"${CMAKE_CURRENT_SOURCE_DIR}/data/knowledge/augments.zh-CN.json")

  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  add_test(NAME augment_import_determinism
    COMMAND "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/knowledge/import_augments_test.py")
  set_tests_properties(augment_import_determinism PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")

  add_test(NAME json_loads_validation
    COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/output/validate_json.py"
            "${LOL_ASSISTANT_ARTIFACT_ROOT}")
  set_tests_properties(json_loads_validation PROPERTIES
    DEPENDS output_tests
    WORKING_DIRECTORY "${LOL_ASSISTANT_ARTIFACT_ROOT}")

  add_test(NAME phase2_benchmark_contract
    COMMAND "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/phase2_benchmark/test_benchmark_phase2.py")
  set_tests_properties(phase2_benchmark_contract PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")

  add_test(NAME recommendation_worker_contract
    COMMAND "${Python3_EXECUTABLE}" -B -m unittest
            tests.test_recommendation_worker -v)
  set_tests_properties(recommendation_worker_contract PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/Scrape")

  add_test(NAME phase3_realtime_contract
    COMMAND "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/phase3_realtime/test_realtime_recommendation.py"
            -v)
  set_tests_properties(phase3_realtime_contract PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")

  add_test(NAME phase4_sidecar_contract
    COMMAND "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/phase4_sidecar/test_sidecar_window.py"
            -v)
  set_tests_properties(phase4_sidecar_contract PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
  add_test(NAME phase4_lcu_champ_select_contract
    COMMAND "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/phase4_sidecar/test_lcu_champ_select.py"
            -v)
  set_tests_properties(phase4_lcu_champ_select_contract PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")

  add_test(NAME phase4_integration_contract
    COMMAND "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/phase4_integration/test_phase4_integration.py"
            -v)
  set_tests_properties(phase4_integration_contract PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
  add_test(NAME recognition_overlay_contract
    COMMAND "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/recognition_overlay/test_recognition_overlay.py"
            -v)
  set_tests_properties(recognition_overlay_contract PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
endif()
