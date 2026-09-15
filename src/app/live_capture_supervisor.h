#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <string>
#include <string_view>

#include "live_control.h"
#include "lol_assistant/capture/desktop_capture_geometry.h"
#include "lol_assistant/common/frame_source.h"

namespace lol_assistant::app {

// Sliding-window restart limiter shared by the live capture backends.  The
// exhausted flag records that a failed recovery consumed the current budget,
// but Refresh/CanSchedule automatically rearms it once old attempts age out.
class RestartAttemptWindow final {
public:
  explicit RestartAttemptWindow(
      std::size_t maximum_attempts = 3U,
      std::chrono::milliseconds window = std::chrono::seconds{60});

  void Refresh(MonotonicTime now) noexcept;
  [[nodiscard]] bool CanSchedule(MonotonicTime now) noexcept;
  [[nodiscard]] bool TryConsume(MonotonicTime now) noexcept;
  void MarkExhausted() noexcept;

  [[nodiscard]] bool exhausted() const noexcept;
  [[nodiscard]] std::size_t attempts_in_window() const noexcept;

private:
  std::size_t maximum_attempts_{3U};
  std::chrono::milliseconds window_{std::chrono::seconds{60}};
  std::deque<MonotonicTime> attempts_{};
  bool exhausted_{false};
};

enum class LiveCapturePolicy {
  AutoDesktopAuthoritative,
  WgcOnly,
  DesktopOnly,
};

enum class CaptureTargetVisibility {
  Visible,
  Minimized,
  Hidden,
  Offscreen,
  CrossOutput,
  Unavailable,
};

struct LiveCaptureDecisionInput final {
  LiveCapturePolicy policy{LiveCapturePolicy::AutoDesktopAuthoritative};
  CaptureTargetVisibility target_visibility{CaptureTargetVisibility::Visible};
  bool wgc_backend_available{false};
  bool desktop_backend_available{false};
  std::optional<FrameFreshnessObservation> latest_wgc{};
  std::optional<FrameFreshnessObservation> latest_desktop{};
  CaptureHealthSnapshot previous{};
  MonotonicTime now{};
  std::chrono::milliseconds maximum_observation_age{750};
};

[[nodiscard]] CaptureTargetVisibility
ToCaptureTargetVisibility(capture::DesktopGeometryStatus status) noexcept;

[[nodiscard]] bool
IsRecentPhysicalObservation(const FrameFreshnessObservation &observation,
                            MonotonicTime now,
                            std::chrono::milliseconds maximum_age) noexcept;

// A backend that is nominally Running/Paused but has produced no physical
// frame for this interval is not useful.  Visibility is part of the contract:
// minimized/hidden/offscreen windows must never burn the recovery budget.
[[nodiscard]] bool ShouldRestartStalledBackend(
    bool backend_started, CaptureTargetVisibility visibility,
    std::optional<MonotonicTime> last_physical_progress, MonotonicTime now,
    std::chrono::milliseconds maximum_silence) noexcept;

// Product policy: a recent Desktop Duplication frame is authoritative in auto
// mode. WGC is selectable only when the desktop backend is unavailable and WGC
// has both a recent physical observation and a producer source timestamp.
[[nodiscard]] CaptureHealthSnapshot
DecideLiveCapture(const LiveCaptureDecisionInput &input);

// Hashes frame contract metadata, provenance, and every buffer byte. Callers
// should invoke it exactly once when dequeuing a new physical frame and retain
// the result alongside that frame while it is pending processor throttling.
[[nodiscard]] std::string
ComputeTransportContentHash(const common::Frame &frame);

struct PendingPhysicalFrame final {
  common::CapturedFrame captured{};
  FrameFreshnessObservation freshness{};
};

// A newest-only mailbox between transport selection and processor throttling.
// It preserves a one-shot physical frame until its due time, expedites it just
// before the freshness deadline, and rejects the same identity until
// AllowReplay() clears that latch for an explicit left-click reread.
class PendingPhysicalFrameBuffer final {
public:
  [[nodiscard]] bool Offer(PendingPhysicalFrame frame,
                           std::string_view selected_source,
                           std::uint64_t selected_epoch);
  [[nodiscard]] std::optional<PendingPhysicalFrame>
  TakeIfDue(MonotonicTime now, MonotonicTime processor_due,
            std::string_view selected_source, std::uint64_t selected_epoch,
            CaptureTargetVisibility visibility,
            std::chrono::milliseconds maximum_age,
            bool ignore_visibility = false);

  void Invalidate() noexcept;
  void AllowReplay() noexcept;
  [[nodiscard]] bool HasPending() const noexcept;
  [[nodiscard]] std::optional<common::FrameIdentity> PendingIdentity() const;

private:
  [[nodiscard]] static bool
  SameIdentity(const common::FrameIdentity &left,
               const common::FrameIdentity &right) noexcept;

  std::optional<PendingPhysicalFrame> pending_{};
  std::optional<common::FrameIdentity> last_processed_{};
};

[[nodiscard]] const char *ToString(CaptureTargetVisibility value) noexcept;

} // namespace lol_assistant::app
