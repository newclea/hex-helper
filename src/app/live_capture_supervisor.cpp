#include "live_capture_supervisor.h"

#include <algorithm>
#include <bit>
#include <cstring>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace lol_assistant::app {
namespace {

[[nodiscard]] bool Processable(
    const std::optional<FrameFreshnessObservation> &observation) noexcept {
  return observation.has_value() && observation->process_allowed;
}

[[nodiscard]] bool StrongWgcProof(
    const std::optional<FrameFreshnessObservation> &observation) noexcept {
  return Processable(observation) && observation->input.source_time.has_value();
}

[[nodiscard]] HealthReason
VisibilityReason(const CaptureTargetVisibility visibility) noexcept {
  switch (visibility) {
  case CaptureTargetVisibility::Minimized:
    return HealthReason::TargetMinimized;
  case CaptureTargetVisibility::Hidden:
    return HealthReason::TargetHidden;
  case CaptureTargetVisibility::Offscreen:
    return HealthReason::TargetOffscreen;
  case CaptureTargetVisibility::CrossOutput:
    return HealthReason::TargetCrossOutput;
  case CaptureTargetVisibility::Visible:
    return HealthReason::NoFreshSource;
  case CaptureTargetVisibility::Unavailable:
  default:
    return HealthReason::TargetUnavailable;
  }
}

void Select(CaptureHealthSnapshot &decision,
            const FrameFreshnessObservation &observation) {
  decision.selected_source = observation.input.source;
  decision.selected_epoch = observation.input.epoch;
}

[[nodiscard]] std::uint64_t Avalanche(std::uint64_t value) noexcept {
  value ^= value >> 33U;
  value *= 0xFF51AFD7ED558CCDULL;
  value ^= value >> 33U;
  value *= 0xC4CEB9FE1A85EC53ULL;
  value ^= value >> 33U;
  return value;
}

void HashLane(std::uint64_t lane, std::uint64_t &first,
              std::uint64_t &second) noexcept {
  lane = Avalanche(lane + 0x9E3779B97F4A7C15ULL);
  first = std::rotl(first ^ lane, 27) * 0x3C79AC492BA7B653ULL + second;
  second = std::rotl(second + lane, 31) * 0x1C69B3F74AC4AE35ULL + first;
}

void HashBytes(const std::uint8_t *bytes, const std::size_t size,
               std::uint64_t &first, std::uint64_t &second) noexcept {
  std::size_t offset = 0U;
  while (size - offset >= sizeof(std::uint64_t)) {
    std::uint64_t lane = 0U;
    std::memcpy(&lane, bytes + offset, sizeof(lane));
    HashLane(lane, first, second);
    offset += sizeof(lane);
  }
  if (offset < size) {
    std::uint64_t tail = 0U;
    const std::size_t remaining = size - offset;
    std::memcpy(&tail, bytes + offset, remaining);
    tail ^= static_cast<std::uint64_t>(remaining) << 56U;
    HashLane(tail, first, second);
  }
  HashLane(static_cast<std::uint64_t>(size), first, second);
}

void HashValue(const std::uint64_t value, std::uint64_t &first,
               std::uint64_t &second) noexcept {
  HashLane(value, first, second);
}

} // namespace

RestartAttemptWindow::RestartAttemptWindow(
    const std::size_t maximum_attempts, const std::chrono::milliseconds window)
    : maximum_attempts_(maximum_attempts), window_(window) {
  if (maximum_attempts_ == 0U || window_.count() <= 0) {
    throw std::invalid_argument(
        "restart attempt window requires a positive limit and duration");
  }
}

void RestartAttemptWindow::Refresh(const MonotonicTime now) noexcept {
  while (!attempts_.empty() && now >= attempts_.front() &&
         now - attempts_.front() >= window_) {
    attempts_.pop_front();
  }
  if (attempts_.size() < maximum_attempts_) {
    exhausted_ = false;
  }
}

bool RestartAttemptWindow::CanSchedule(const MonotonicTime now) noexcept {
  Refresh(now);
  if (attempts_.size() >= maximum_attempts_) {
    exhausted_ = true;
  }
  return !exhausted_;
}

bool RestartAttemptWindow::TryConsume(const MonotonicTime now) noexcept {
  if (!CanSchedule(now)) {
    return false;
  }
  attempts_.push_back(now);
  return true;
}

void RestartAttemptWindow::MarkExhausted() noexcept { exhausted_ = true; }

bool RestartAttemptWindow::exhausted() const noexcept { return exhausted_; }

std::size_t RestartAttemptWindow::attempts_in_window() const noexcept {
  return attempts_.size();
}

CaptureTargetVisibility ToCaptureTargetVisibility(
    const capture::DesktopGeometryStatus status) noexcept {
  using capture::DesktopGeometryStatus;
  switch (status) {
  case DesktopGeometryStatus::Ready:
    return CaptureTargetVisibility::Visible;
  case DesktopGeometryStatus::Minimized:
    return CaptureTargetVisibility::Minimized;
  case DesktopGeometryStatus::Hidden:
    return CaptureTargetVisibility::Hidden;
  case DesktopGeometryStatus::Offscreen:
  case DesktopGeometryStatus::PartiallyOffscreen:
    return CaptureTargetVisibility::Offscreen;
  case DesktopGeometryStatus::CrossOutput:
    return CaptureTargetVisibility::CrossOutput;
  case DesktopGeometryStatus::InvalidTarget:
  case DesktopGeometryStatus::NoOutputs:
  default:
    return CaptureTargetVisibility::Unavailable;
  }
}

bool IsRecentPhysicalObservation(
    const FrameFreshnessObservation &observation, const MonotonicTime now,
    const std::chrono::milliseconds maximum_age) noexcept {
  if (!observation.process_allowed || maximum_age.count() < 0 ||
      now < observation.input.observed_at) {
    return false;
  }
  // The configured 750 ms boundary is inclusive so a decision made on the
  // boundary can consume the frame; the first tick beyond it invalidates it.
  return now - observation.input.observed_at <= maximum_age;
}

bool ShouldRestartStalledBackend(
    const bool backend_started, const CaptureTargetVisibility visibility,
    const std::optional<MonotonicTime> last_physical_progress,
    const MonotonicTime now,
    const std::chrono::milliseconds maximum_silence) noexcept {
  return backend_started && visibility == CaptureTargetVisibility::Visible &&
         last_physical_progress.has_value() && maximum_silence.count() >= 0 &&
         now >= *last_physical_progress &&
         now - *last_physical_progress > maximum_silence;
}

CaptureHealthSnapshot DecideLiveCapture(const LiveCaptureDecisionInput &input) {
  CaptureHealthSnapshot decision;
  if (input.target_visibility != CaptureTargetVisibility::Visible) {
    decision.state = CaptureHealthState::Degraded;
    decision.reason = VisibilityReason(input.target_visibility);
  } else {
    const auto recent =
        [&](const std::optional<FrameFreshnessObservation> &observation)
        -> std::optional<FrameFreshnessObservation> {
      if (!observation.has_value() ||
          !IsRecentPhysicalObservation(*observation, input.now,
                                       input.maximum_observation_age)) {
        return std::nullopt;
      }
      return observation;
    };
    const auto wgc = recent(input.latest_wgc);
    const auto desktop = recent(input.latest_desktop);
    const bool desktop_fresh = Processable(desktop);
    const bool wgc_strong = StrongWgcProof(wgc);

    switch (input.policy) {
    case LiveCapturePolicy::AutoDesktopAuthoritative:
      if (desktop_fresh) {
        decision.state = CaptureHealthState::DesktopFallback;
        decision.reason = HealthReason::DesktopAuthoritative;
        Select(decision, *desktop);
      } else if (!input.desktop_backend_available && wgc_strong) {
        decision.state = CaptureHealthState::Degraded;
        decision.reason = HealthReason::DesktopUnavailableWgcFresh;
        Select(decision, *wgc);
      } else {
        decision.state = input.desktop_backend_available
                             ? CaptureHealthState::Suspect
                             : CaptureHealthState::Degraded;
        decision.reason =
            Processable(wgc) && !wgc->input.source_time.has_value()
                ? HealthReason::WgcFreshnessUnproven
                : HealthReason::NoFreshSource;
        decision.request_wgc_restart = !input.desktop_backend_available &&
                                       input.wgc_backend_available &&
                                       !wgc_strong;
      }
      break;
    case LiveCapturePolicy::WgcOnly:
      if (wgc_strong) {
        decision.state = CaptureHealthState::Healthy;
        decision.reason = HealthReason::WgcFresh;
        Select(decision, *wgc);
      } else {
        decision.state = CaptureHealthState::Degraded;
        decision.reason =
            Processable(wgc) && !wgc->input.source_time.has_value()
                ? HealthReason::WgcFreshnessUnproven
                : (input.wgc_backend_available ? HealthReason::WgcStalled
                                               : HealthReason::WgcUnavailable);
        decision.request_wgc_restart = input.wgc_backend_available;
      }
      break;
    case LiveCapturePolicy::DesktopOnly:
      if (desktop_fresh) {
        decision.state = CaptureHealthState::Healthy;
        decision.reason = HealthReason::DesktopAuthoritative;
        Select(decision, *desktop);
      } else {
        decision.state = CaptureHealthState::Suspect;
        decision.reason = HealthReason::NoFreshSource;
      }
      break;
    }
  }

  decision.switched_source =
      input.previous.selected_source != decision.selected_source ||
      input.previous.selected_epoch != decision.selected_epoch;
  decision.revoke_recommendation =
      input.previous.selected_source.has_value() &&
      (decision.switched_source || !decision.selected_source.has_value());
  return decision;
}

std::string ComputeTransportContentHash(const common::Frame &frame) {
  std::uint64_t first = 0x243F6A8885A308D3ULL;
  std::uint64_t second = 0x13198A2E03707344ULL;
  HashValue(0x5452414E53504F52ULL, first, second);
  HashValue(static_cast<std::uint64_t>(frame.source.kind), first, second);
  HashBytes(reinterpret_cast<const std::uint8_t *>(frame.source.id.data()),
            frame.source.id.size(), first, second);
  HashValue(frame.width, first, second);
  HashValue(frame.height, first, second);
  HashValue(frame.stride, first, second);
  HashValue(static_cast<std::uint64_t>(frame.buffer.size()), first, second);
  if (!frame.buffer.empty()) {
    HashBytes(frame.buffer.data(), frame.buffer.size(), first, second);
  }
  first = Avalanche(first ^ std::rotl(second, 17));
  second = Avalanche(second ^ std::rotl(first, 29));
  std::ostringstream output;
  output << std::hex << std::setw(16) << std::setfill('0') << first
         << std::setw(16) << second;
  return output.str();
}

bool PendingPhysicalFrameBuffer::Offer(PendingPhysicalFrame frame,
                                       const std::string_view selected_source,
                                       const std::uint64_t selected_epoch) {
  if (!frame.captured.IsValid() || !frame.freshness.process_allowed ||
      selected_source.empty() ||
      frame.freshness.input.source != selected_source ||
      frame.freshness.input.epoch != selected_epoch ||
      frame.freshness.input.epoch != frame.captured.identity.capture_epoch ||
      frame.freshness.input.progress != frame.captured.identity.frame_id) {
    return false;
  }
  const auto &identity = frame.captured.identity;
  if (last_processed_.has_value() && SameIdentity(*last_processed_, identity)) {
    return false;
  }
  if (pending_.has_value()) {
    const auto &pending_identity = pending_->captured.identity;
    const bool same_generation =
        pending_->freshness.input.source == selected_source &&
        pending_identity.capture_epoch == selected_epoch;
    if (!same_generation) {
      pending_.reset();
    } else if (identity.frame_id <= pending_identity.frame_id) {
      return false;
    }
  }
  pending_ = std::move(frame);
  return true;
}

std::optional<PendingPhysicalFrame> PendingPhysicalFrameBuffer::TakeIfDue(
    const MonotonicTime now, const MonotonicTime processor_due,
    const std::string_view selected_source, const std::uint64_t selected_epoch,
    const CaptureTargetVisibility visibility,
    const std::chrono::milliseconds maximum_age,
    const bool ignore_visibility) {
  if (!pending_.has_value()) {
    return std::nullopt;
  }
  if ((!ignore_visibility &&
       visibility != CaptureTargetVisibility::Visible) ||
      selected_source.empty() ||
      pending_->freshness.input.source != selected_source ||
      pending_->freshness.input.epoch != selected_epoch ||
      maximum_age.count() < 0 || now < pending_->freshness.input.observed_at ||
      now - pending_->freshness.input.observed_at > maximum_age) {
    pending_.reset();
    return std::nullopt;
  }

  const auto safety_margin =
      std::min(maximum_age, std::chrono::milliseconds{1});
  const auto freshness_due =
      pending_->freshness.input.observed_at + maximum_age - safety_margin;
  const auto effective_due = std::min(processor_due, freshness_due);
  if (now < effective_due) {
    return std::nullopt;
  }

  std::optional<PendingPhysicalFrame> result{std::move(*pending_)};
  pending_.reset();
  last_processed_ = result->captured.identity;
  return result;
}

void PendingPhysicalFrameBuffer::Invalidate() noexcept { pending_.reset(); }

void PendingPhysicalFrameBuffer::AllowReplay() noexcept {
  last_processed_.reset();
}

bool PendingPhysicalFrameBuffer::HasPending() const noexcept {
  return pending_.has_value();
}

std::optional<common::FrameIdentity>
PendingPhysicalFrameBuffer::PendingIdentity() const {
  if (!pending_.has_value()) {
    return std::nullopt;
  }
  return pending_->captured.identity;
}

bool PendingPhysicalFrameBuffer::SameIdentity(
    const common::FrameIdentity &left,
    const common::FrameIdentity &right) noexcept {
  return left.source.kind == right.source.kind &&
         left.source.id == right.source.id &&
         left.capture_epoch == right.capture_epoch &&
         left.frame_id == right.frame_id;
}

const char *ToString(const CaptureTargetVisibility value) noexcept {
  switch (value) {
  case CaptureTargetVisibility::Visible:
    return "visible";
  case CaptureTargetVisibility::Minimized:
    return "minimized";
  case CaptureTargetVisibility::Hidden:
    return "hidden";
  case CaptureTargetVisibility::Offscreen:
    return "offscreen";
  case CaptureTargetVisibility::CrossOutput:
    return "cross_output";
  case CaptureTargetVisibility::Unavailable:
    return "unavailable";
  }
  return "unavailable";
}

} // namespace lol_assistant::app
