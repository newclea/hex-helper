#pragma once

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <chrono>
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "lol_assistant/capture/bounded_queue.h"
#include "lol_assistant/capture/desktop_duplication_source.h"

namespace lol_assistant::capture::detail {

struct DesktopBackendFrame final {
  std::uint32_t width{0U};
  std::uint32_t height{0U};
  std::uint32_t stride{0U};
  std::vector<std::uint8_t> buffer{};
  std::optional<std::chrono::nanoseconds> source_timestamp{};
  bool protected_content_masked{false};
};

struct DesktopAcquireResult final {
  HRESULT result{E_FAIL};
  bool frame_acquired{false};
  DesktopBackendFrame frame{};
  std::string diagnostic{};
};

class IDesktopDuplicationBackend {
 public:
  virtual ~IDesktopDuplicationBackend() = default;

  [[nodiscard]] virtual HRESULT Initialize(
      HMONITOR monitor, bool recreate_device,
      std::string* error) noexcept = 0;
  [[nodiscard]] virtual DesktopAcquireResult AcquireNextFrame(
      std::chrono::milliseconds timeout,
      const DesktopCaptureGeometry& geometry) noexcept = 0;
  [[nodiscard]] virtual HRESULT ReleaseFrame() noexcept = 0;
  virtual void Shutdown() noexcept = 0;
};

[[nodiscard]] std::unique_ptr<IDesktopDuplicationBackend>
CreateDxgiDesktopDuplicationBackend();

class DesktopDuplicationBackendController final {
 public:
  DesktopDuplicationBackendController(
      DesktopDuplicationOptions options,
      std::unique_ptr<IDesktopDuplicationBackend> backend,
      common::FrameSource source = {
          common::FrameSourceKind::DesktopDuplication,
          "desktop-duplication"});
  ~DesktopDuplicationBackendController();

  DesktopDuplicationBackendController(
      const DesktopDuplicationBackendController&) = delete;
  DesktopDuplicationBackendController& operator=(
      const DesktopDuplicationBackendController&) = delete;

  [[nodiscard]] bool Start(const DesktopCaptureGeometry& geometry,
                           std::string* error) noexcept;
  [[nodiscard]] bool Poll(const DesktopCaptureGeometry& geometry) noexcept;
  void Stop() noexcept;

  [[nodiscard]] CaptureState State() const noexcept;
  [[nodiscard]] std::string LastError() const noexcept;
  [[nodiscard]] DesktopDuplicationStatistics Statistics() const noexcept;
  [[nodiscard]] std::optional<DesktopCaptureGeometry> Geometry() const noexcept;
  [[nodiscard]] std::optional<common::CapturedFrame> TryGetNextFrame();
  [[nodiscard]] std::uint64_t CurrentCaptureEpoch() const noexcept;

 private:
  [[nodiscard]] bool InitializeBackend(const DesktopCaptureGeometry& geometry,
                                       bool recreate_device,
                                       bool is_recovery,
                                       std::string* error) noexcept;
  [[nodiscard]] bool Recover(const DesktopCaptureGeometry& geometry,
                             HRESULT failure) noexcept;
  void ClearQueuedFrames() noexcept;
  void SetError(std::string message) noexcept;
  [[nodiscard]] common::FrameSource SourceForEpoch(
      std::uint64_t epoch) const;

  DesktopDuplicationOptions options_{};
  std::unique_ptr<IDesktopDuplicationBackend> backend_{};
  BoundedQueue<common::CapturedFrame> frames_;
  common::FrameSource base_source_{
      common::FrameSourceKind::DesktopDuplication,
      "desktop-duplication"};

  mutable std::mutex mutex_{};
  CaptureState state_{CaptureState::Stopped};
  std::string last_error_{};
  DesktopDuplicationStatistics statistics_{};
  std::optional<DesktopCaptureGeometry> geometry_{};
  std::optional<std::chrono::steady_clock::time_point> latest_frame_time_{};
  HMONITOR active_monitor_{nullptr};
  std::uint64_t capture_epoch_{0U};
  std::uint64_t next_frame_id_{1U};
};

}  // namespace lol_assistant::capture::detail
