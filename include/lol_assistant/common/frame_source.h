#pragma once

#include <cstdint>
#include <optional>
#include <utility>

#include "lol_assistant/common/frame.h"

namespace lol_assistant::common {

struct FrameIdentity final {
  FrameSource source{};
  std::uint64_t capture_epoch{0U};
  std::uint64_t frame_id{0U};

  [[nodiscard]] bool IsValid() const noexcept {
    if (!source.IsValid() || frame_id == 0U) {
      return false;
    }
    const bool live =
        source.kind == FrameSourceKind::WindowsGraphicsCapture ||
        source.kind == FrameSourceKind::DesktopDuplication;
    return !live || capture_epoch != 0U;
  }
};

struct CapturedFrame final {
  Frame frame{};
  FrameIdentity identity{};

  [[nodiscard]] bool IsValid() const noexcept {
    return frame.IsValid() && identity.IsValid() &&
           identity.source.kind == frame.source.kind &&
           identity.source.id == frame.source.id &&
           identity.frame_id == frame.frame_id;
  }
};

// Implementations may return nullopt when no frame is currently available.
// Capture lifecycle and retry policy intentionally remain module concerns.
class IFrameSource {
 public:
  virtual ~IFrameSource() = default;

  [[nodiscard]] virtual FrameSource Source() const = 0;
  [[nodiscard]] virtual std::optional<Frame> TryGetNextFrame() = 0;

  // Live sources override this to preserve the source epoch as part of frame
  // identity. Epochless sources retain the legacy Frame-only contract.
  [[nodiscard]] virtual std::optional<CapturedFrame>
  TryGetNextCapturedFrame() {
    auto frame = TryGetNextFrame();
    if (!frame.has_value()) {
      return std::nullopt;
    }
    CapturedFrame captured;
    captured.identity = FrameIdentity{frame->source, 0U, frame->frame_id};
    captured.frame = std::move(*frame);
    return captured;
  }

  [[nodiscard]] virtual std::uint64_t CurrentCaptureEpoch() const noexcept {
    return 0U;
  }
};

}  // namespace lol_assistant::common
