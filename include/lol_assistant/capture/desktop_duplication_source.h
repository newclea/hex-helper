#pragma once

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>

#include "lol_assistant/capture/capture_state_machine.h"
#include "lol_assistant/capture/desktop_capture_geometry.h"
#include "lol_assistant/common/frame_source.h"

namespace lol_assistant::capture {

struct DesktopDuplicationOptions final {
  std::size_t queue_capacity{3U};
  std::chrono::milliseconds acquire_timeout{16};
  std::chrono::milliseconds recovery_backoff{250};
  PartialVisibilityPolicy partial_visibility{
      PartialVisibilityPolicy::PauseUnlessFullyVisible};
  bool include_pointer{false};
};

struct DesktopDuplicationStatistics final {
  std::uint64_t acquired{0U};
  std::uint64_t released{0U};
  std::uint64_t converted{0U};
  std::uint64_t dropped{0U};
  std::uint64_t wait_timeouts{0U};
  std::uint64_t access_lost{0U};
  std::uint64_t session_disconnects{0U};
  std::uint64_t device_removed{0U};
  std::uint64_t duplication_recreates{0U};
  std::uint64_t device_recreates{0U};
  std::uint64_t protected_content_frames{0U};
  std::size_t queued_frames{0U};
  std::size_t queue_capacity{0U};
  std::uint64_t capture_epoch{0U};
  std::optional<std::chrono::milliseconds> latest_frame_age{};
};

class DesktopDuplicationSource final : public common::IFrameSource {
 public:
  static constexpr std::size_t kMaximumQueueCapacity = 3U;
  static constexpr std::size_t kDefaultQueueCapacity = 3U;

  explicit DesktopDuplicationSource(
      DesktopCaptureTarget target,
      DesktopDuplicationOptions options = {});
  ~DesktopDuplicationSource() override;

  DesktopDuplicationSource(const DesktopDuplicationSource&) = delete;
  DesktopDuplicationSource& operator=(const DesktopDuplicationSource&) = delete;
  DesktopDuplicationSource(DesktopDuplicationSource&&) = delete;
  DesktopDuplicationSource& operator=(DesktopDuplicationSource&&) = delete;

  [[nodiscard]] static bool IsSupported(
      const DesktopCaptureTarget& target,
      std::string* reason = nullptr) noexcept;
  [[nodiscard]] bool Start(std::string* error = nullptr) noexcept;
  void Stop() noexcept;

  [[nodiscard]] CaptureState State() const noexcept;
  [[nodiscard]] std::string LastError() const noexcept;
  [[nodiscard]] DesktopDuplicationStatistics Statistics() const noexcept;
  [[nodiscard]] std::optional<DesktopCaptureGeometry> Geometry() const noexcept;

  [[nodiscard]] common::FrameSource Source() const override;
  [[nodiscard]] std::optional<common::Frame> TryGetNextFrame() override;
  [[nodiscard]] std::optional<common::CapturedFrame>
  TryGetNextCapturedFrame() override;
  [[nodiscard]] std::uint64_t CurrentCaptureEpoch() const noexcept override;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace lol_assistant::capture
