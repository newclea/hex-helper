#pragma once

#include <windows.h>

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>

#include "lol_assistant/capture/capture_state_machine.h"
#include "lol_assistant/common/frame_source.h"

namespace lol_assistant::capture {

namespace detail {

enum class WgcFramePublishResult : std::uint8_t {
  Published,
  Replaced,
  RejectedEpoch,
  InvalidFrame,
};

// Testable epoch seam shared by the real FrameArrived path. BeginCaptureEpoch
// clears the old queue; Publish rejects callbacks carrying any older token.
class WgcCapturedFrameMailbox final {
 public:
  [[nodiscard]] std::uint64_t BeginCaptureEpoch() noexcept;
  void Clear() noexcept;
  [[nodiscard]] bool IsCurrent(std::uint64_t epoch) const noexcept;
  [[nodiscard]] std::uint64_t CurrentCaptureEpoch() const noexcept;
  [[nodiscard]] WgcFramePublishResult Publish(
      std::uint64_t callback_epoch, common::CapturedFrame frame);
  [[nodiscard]] std::optional<common::CapturedFrame> Take();

 private:
  mutable std::mutex mutex_{};
  std::uint64_t current_epoch_{0U};
  std::optional<common::CapturedFrame> latest_{};
};

}  // namespace detail

struct CaptureStatistics final {
  std::uint64_t received{0U};
  std::uint64_t converted{0U};
  std::uint64_t dropped{0U};
  std::size_t queued_frames{0U};
  std::size_t queue_capacity{0U};
  std::optional<std::chrono::milliseconds> latest_frame_age{};
  std::optional<double> conversion_p50_ms{};
  std::optional<double> conversion_p95_ms{};
};

class WindowsGraphicsCaptureSource final
    : public common::IFrameSource {
 public:
  static constexpr std::size_t kMaximumQueueCapacity = 3U;
  static constexpr std::size_t kDefaultQueueCapacity = 3U;

  explicit WindowsGraphicsCaptureSource(
      HWND window, std::size_t queue_capacity = kDefaultQueueCapacity);
  ~WindowsGraphicsCaptureSource() override;

  WindowsGraphicsCaptureSource(const WindowsGraphicsCaptureSource&) = delete;
  WindowsGraphicsCaptureSource& operator=(
      const WindowsGraphicsCaptureSource&) = delete;
  WindowsGraphicsCaptureSource(WindowsGraphicsCaptureSource&&) = delete;
  WindowsGraphicsCaptureSource& operator=(
      WindowsGraphicsCaptureSource&&) = delete;

  [[nodiscard]] static bool IsSupported(
      std::string* reason = nullptr) noexcept;

  [[nodiscard]] bool Start(std::string* error = nullptr) noexcept;
  void Stop() noexcept;

  [[nodiscard]] CaptureState State() const noexcept;
  [[nodiscard]] std::string LastError() const noexcept;
  [[nodiscard]] CaptureStatistics Statistics() const noexcept;

  [[nodiscard]] common::FrameSource Source() const override;
  [[nodiscard]] std::optional<common::Frame> TryGetNextFrame() override;
  [[nodiscard]] std::optional<common::CapturedFrame>
  TryGetNextCapturedFrame() override;
  [[nodiscard]] std::uint64_t CurrentCaptureEpoch() const noexcept override;

 private:
  class Impl;
  std::shared_ptr<Impl> impl_;
};

}  // namespace lol_assistant::capture
