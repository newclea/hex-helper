#include "desktop_duplication_backend.h"

#include <dxgi.h>

#include <cstdint>
#include <deque>
#include <iostream>
#include <memory>
#include <string>
#include <utility>

namespace {

using lol_assistant::capture::DesktopCaptureGeometry;
using lol_assistant::capture::DesktopDuplicationOptions;
using lol_assistant::capture::DesktopDuplicationStatistics;
using lol_assistant::capture::DesktopGeometryStatus;
using lol_assistant::capture::detail::DesktopAcquireResult;
using lol_assistant::capture::detail::DesktopBackendFrame;
using lol_assistant::capture::detail::DesktopDuplicationBackendController;
using lol_assistant::capture::detail::IDesktopDuplicationBackend;

int failures = 0;

void Check(const bool condition, const char* const expression) {
  if (!condition) {
    ++failures;
    std::cerr << "[FAIL] " << expression << '\n';
  }
}

#define CHECK(expression) Check((expression), #expression)

DesktopCaptureGeometry ReadyGeometry() {
  DesktopCaptureGeometry geometry{};
  geometry.status = DesktopGeometryStatus::Ready;
  geometry.monitor = reinterpret_cast<HMONITOR>(std::uintptr_t{1U});
  geometry.output_physical = RECT{0, 0, 2560, 1600};
  geometry.requested_physical = RECT{100, 100, 104, 102};
  geometry.visible_physical = geometry.requested_physical;
  geometry.source_rect = RECT{100, 100, 104, 102};
  geometry.fully_visible = true;
  return geometry;
}

DesktopAcquireResult FrameResult(const std::uint8_t value) {
  DesktopBackendFrame frame{};
  frame.width = 4U;
  frame.height = 2U;
  frame.stride = 16U;
  frame.buffer.assign(32U, value);
  frame.source_timestamp = std::chrono::nanoseconds{value};
  return DesktopAcquireResult{S_OK, true, std::move(frame), {}};
}

class FakeBackend final : public IDesktopDuplicationBackend {
 public:
  HRESULT Initialize(const HMONITOR monitor, const bool recreate_device,
                     std::string*) noexcept override {
    ++initialize_count;
    if (recreate_device) {
      ++device_initialize_count;
    } else {
      ++duplication_initialize_count;
    }
    current_monitor = monitor;
    return initialize_result;
  }

  DesktopAcquireResult AcquireNextFrame(
      std::chrono::milliseconds,
      const DesktopCaptureGeometry&) noexcept override {
    ++acquire_calls;
    if (results.empty()) {
      return {DXGI_ERROR_WAIT_TIMEOUT, false, {}, {}};
    }
    auto result = std::move(results.front());
    results.pop_front();
    return result;
  }

  HRESULT ReleaseFrame() noexcept override {
    ++release_calls;
    return S_OK;
  }

  void Shutdown() noexcept override {
    ++shutdown_count;
    current_monitor = nullptr;
  }

  std::deque<DesktopAcquireResult> results{};
  HRESULT initialize_result{S_OK};
  std::uint64_t initialize_count{0U};
  std::uint64_t device_initialize_count{0U};
  std::uint64_t duplication_initialize_count{0U};
  std::uint64_t acquire_calls{0U};
  std::uint64_t release_calls{0U};
  std::uint64_t shutdown_count{0U};
  HMONITOR current_monitor{nullptr};
};

void TestTimeoutQueueAndReleaseBalance() {
  auto backend = std::make_unique<FakeBackend>();
  auto* const fake = backend.get();
  fake->results.push_back(
      {DXGI_ERROR_WAIT_TIMEOUT, false, {}, "expected timeout"});
  fake->results.push_back(FrameResult(1U));
  fake->results.push_back(FrameResult(2U));
  fake->results.push_back(FrameResult(3U));

  DesktopDuplicationOptions options{};
  options.queue_capacity = 2U;
  DesktopDuplicationBackendController controller(options, std::move(backend));
  std::string error;
  const auto geometry = ReadyGeometry();
  CHECK(controller.Start(geometry, &error));
  CHECK(error.empty());
  CHECK(controller.Poll(geometry));
  CHECK(controller.Poll(geometry));
  CHECK(controller.Poll(geometry));
  CHECK(controller.Poll(geometry));

  const DesktopDuplicationStatistics statistics = controller.Statistics();
  CHECK(statistics.wait_timeouts == 1U);
  CHECK(statistics.acquired == 3U);
  CHECK(statistics.released == 3U);
  CHECK(statistics.converted == 3U);
  CHECK(statistics.dropped == 1U);
  CHECK(statistics.queued_frames == 2U);
  CHECK(fake->acquire_calls == 4U);
  CHECK(fake->release_calls == 3U);
  std::cout << "[FAKE] queue acquired=" << statistics.acquired
            << " released=" << statistics.released
            << " converted=" << statistics.converted
            << " dropped=" << statistics.dropped
            << " timeouts=" << statistics.wait_timeouts << '\n';
  auto newest = controller.TryGetNextFrame();
  CHECK(newest.has_value());
  CHECK(newest.has_value() && newest->frame.frame_id == 3U);
  CHECK(controller.Statistics().queued_frames == 0U);
}

void TestRecoverableFailuresAndReleaseOnConversionFailure() {
  auto backend = std::make_unique<FakeBackend>();
  auto* const fake = backend.get();
  fake->results.push_back(
      {DXGI_ERROR_ACCESS_LOST, false, {}, "access lost"});
  fake->results.push_back(
      {DXGI_ERROR_SESSION_DISCONNECTED, false, {}, "disconnected"});
  fake->results.push_back(
      {DXGI_ERROR_DEVICE_REMOVED, false, {}, "device removed"});
  fake->results.push_back({E_FAIL, true, {}, "copy failed after acquire"});
  fake->results.push_back(FrameResult(9U));

  DesktopDuplicationOptions options{};
  options.recovery_backoff = std::chrono::milliseconds{0};
  DesktopDuplicationBackendController controller(options, std::move(backend));
  const auto geometry = ReadyGeometry();
  std::string error;
  CHECK(controller.Start(geometry, &error));
  CHECK(controller.Poll(geometry));
  CHECK(controller.Poll(geometry));
  CHECK(controller.Poll(geometry));
  CHECK(!controller.Poll(geometry));
  CHECK(controller.Poll(geometry));

  const auto statistics = controller.Statistics();
  CHECK(statistics.access_lost == 1U);
  CHECK(statistics.session_disconnects == 1U);
  CHECK(statistics.device_removed == 1U);
  CHECK(statistics.duplication_recreates == 2U);
  CHECK(statistics.device_recreates == 1U);
  CHECK(statistics.acquired == 2U);
  CHECK(statistics.released == 2U);
  CHECK(fake->duplication_initialize_count == 2U);
  CHECK(fake->device_initialize_count == 2U);
  CHECK(fake->release_calls == 2U);
  std::cout << "[FAKE] recovery acquired=" << statistics.acquired
            << " released=" << statistics.released
            << " access_lost=" << statistics.access_lost
            << " session_disconnects=" << statistics.session_disconnects
            << " device_removed=" << statistics.device_removed
            << " duplication_recreates="
            << statistics.duplication_recreates
            << " device_recreates=" << statistics.device_recreates << '\n';
}

}  // namespace

int main() {
  TestTimeoutQueueAndReleaseBalance();
  TestRecoverableFailuresAndReleaseOnConversionFailure();
  std::cout << "desktop_duplication_test: failures=" << failures << '\n';
  return failures == 0 ? 0 : 1;
}
