#include "live_control.h"

#include <algorithm>
#include <limits>
#include <mutex>
#include <unordered_map>
#include <utility>

namespace lol_assistant::app {
namespace {

std::int64_t MillisecondsSinceEpoch(const MonotonicTime time) noexcept {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             time.time_since_epoch())
      .count();
}

std::int64_t NonNegativeAgeMs(const MonotonicTime now,
                              const MonotonicTime then) noexcept {
  return std::max<std::int64_t>(
      0, std::chrono::duration_cast<std::chrono::milliseconds>(now - then)
             .count());
}

bool IsFresh(const std::optional<FrameFreshnessObservation>& observation) {
  return observation.has_value() && observation->process_allowed;
}

}  // namespace

struct FrameFreshnessGate::Impl final {
  struct SourceState final {
    std::uint64_t epoch{0U};
    std::optional<std::int64_t> source_time{};
    std::string content_hash{};
    std::uint64_t progress{0U};
    FrameFreshness last_freshness{FrameFreshness::InvalidSource};
    std::uint64_t accepted_frames{0U};
    std::uint64_t rejected_frames{0U};
    MonotonicTime last_fresh_at{};
  };

  mutable std::mutex mutex{};
  std::unordered_map<std::string, SourceState> sources{};
  std::uint64_t accepted_sequence{0U};
};

FrameFreshnessGate::FrameFreshnessGate() : impl_(std::make_unique<Impl>()) {}
FrameFreshnessGate::~FrameFreshnessGate() = default;
FrameFreshnessGate::FrameFreshnessGate(FrameFreshnessGate&&) noexcept =
    default;
FrameFreshnessGate& FrameFreshnessGate::operator=(
    FrameFreshnessGate&&) noexcept = default;

FrameFreshnessObservation FrameFreshnessGate::Observe(
    const FrameTransportInput& input) {
  std::lock_guard lock(impl_->mutex);
  FrameFreshnessObservation result;
  result.input = input;
  result.accepted_sequence = impl_->accepted_sequence;
  if (input.source.empty()) {
    result.freshness = FrameFreshness::InvalidSource;
    return result;
  }

  const auto found = impl_->sources.find(input.source);
  if (found == impl_->sources.end()) {
    Impl::SourceState state;
    state.epoch = input.epoch;
    state.source_time = input.source_time;
    state.content_hash = input.content_hash;
    state.progress = input.progress;
    state.last_freshness = input.source_time.has_value()
                               ? FrameFreshness::FreshNewEpoch
                               : FrameFreshness::FreshSourceTimeMissing;
    state.accepted_frames = 1U;
    state.last_fresh_at = input.observed_at;
    ++impl_->accepted_sequence;
    result.freshness = state.last_freshness;
    result.process_allowed = true;
    result.content_changed = true;
    result.accepted_sequence = impl_->accepted_sequence;
    result.source_accepted_frames = state.accepted_frames;
    impl_->sources.emplace(input.source, std::move(state));
    return result;
  }

  auto& state = found->second;
  const auto reject = [&](const FrameFreshness freshness) {
    state.last_freshness = freshness;
    ++state.rejected_frames;
    result.freshness = freshness;
    result.source_accepted_frames = state.accepted_frames;
    result.source_rejected_frames = state.rejected_frames;
  };
  const auto accept = [&](const FrameFreshness freshness,
                          const bool new_epoch) {
    const bool hash_changed =
        new_epoch || input.content_hash != state.content_hash;
    state.epoch = input.epoch;
    if (input.source_time.has_value()) {
      state.source_time = input.source_time;
    } else if (new_epoch) {
      state.source_time.reset();
    }
    state.content_hash = input.content_hash;
    state.progress = input.progress;
    state.last_freshness = freshness;
    state.last_fresh_at = input.observed_at;
    ++state.accepted_frames;
    ++impl_->accepted_sequence;
    result.freshness = freshness;
    result.process_allowed = true;
    result.content_changed = hash_changed;
    result.accepted_sequence = impl_->accepted_sequence;
    result.source_accepted_frames = state.accepted_frames;
    result.source_rejected_frames = state.rejected_frames;
  };

  if (input.epoch < state.epoch) {
    reject(FrameFreshness::RegressedEpoch);
    return result;
  }
  if (input.epoch > state.epoch) {
    accept(input.source_time.has_value() ? FrameFreshness::FreshNewEpoch
                                         : FrameFreshness::FreshSourceTimeMissing,
           true);
    return result;
  }

  if (input.source_time.has_value() && state.source_time.has_value()) {
    if (*input.source_time < *state.source_time) {
      reject(FrameFreshness::RegressedSourceTime);
      return result;
    }
    if (*input.source_time == *state.source_time) {
      reject(FrameFreshness::DuplicateSourceTime);
      return result;
    }
  }

  if (input.progress < state.progress) {
    reject(FrameFreshness::RegressedProgress);
    return result;
  }
  if (input.progress == state.progress) {
    reject(FrameFreshness::DuplicateProgress);
    return result;
  }

  accept(input.source_time.has_value() ? FrameFreshness::Fresh
                                       : FrameFreshness::FreshSourceTimeMissing,
         false);
  return result;
}

std::optional<FrameFreshnessSnapshot> FrameFreshnessGate::Snapshot(
    const std::string_view source, const MonotonicTime now) const {
  std::lock_guard lock(impl_->mutex);
  const auto found = impl_->sources.find(std::string{source});
  if (found == impl_->sources.end()) {
    return std::nullopt;
  }
  const auto& state = found->second;
  FrameFreshnessSnapshot snapshot;
  snapshot.source = found->first;
  snapshot.epoch = state.epoch;
  snapshot.source_time = state.source_time;
  snapshot.content_hash = state.content_hash;
  snapshot.progress = state.progress;
  snapshot.last_freshness = state.last_freshness;
  snapshot.accepted_frames = state.accepted_frames;
  snapshot.rejected_frames = state.rejected_frames;
  if (state.accepted_frames > 0U) {
    snapshot.last_fresh_age_ms = NonNegativeAgeMs(now, state.last_fresh_at);
  }
  return snapshot;
}

struct DualSourceArbiter::Impl final {
  mutable std::mutex mutex{};
  CaptureHealthSnapshot snapshot{};
};

DualSourceArbiter::DualSourceArbiter() : impl_(std::make_unique<Impl>()) {}
DualSourceArbiter::~DualSourceArbiter() = default;
DualSourceArbiter::DualSourceArbiter(DualSourceArbiter&&) noexcept = default;
DualSourceArbiter& DualSourceArbiter::operator=(
    DualSourceArbiter&&) noexcept = default;

HealthDecision DualSourceArbiter::Decide(
    const FrameFreshnessObservation& wgc,
    const FrameFreshnessObservation& desktop) {
  return Decide(std::optional<FrameFreshnessObservation>{wgc},
                std::optional<FrameFreshnessObservation>{desktop});
}

HealthDecision DualSourceArbiter::Decide(
    const std::optional<FrameFreshnessObservation>& wgc,
    const std::optional<FrameFreshnessObservation>& desktop) {
  std::lock_guard lock(impl_->mutex);
  const auto previous_source = impl_->snapshot.selected_source;
  const auto previous_epoch = impl_->snapshot.selected_epoch;
  CaptureHealthSnapshot decision;

  const auto select = [&](const FrameFreshnessObservation& observation) {
    decision.selected_source = observation.input.source;
    decision.selected_epoch = observation.input.epoch;
  };
  if (IsFresh(desktop)) {
    decision.state = CaptureHealthState::DesktopFallback;
    decision.reason = HealthReason::DesktopAuthoritative;
    select(*desktop);
  } else if (IsFresh(wgc) && wgc->input.source_time.has_value()) {
    decision.state = CaptureHealthState::Degraded;
    decision.reason = HealthReason::DesktopUnavailableWgcFresh;
    select(*wgc);
  } else {
    decision.state = CaptureHealthState::Degraded;
    decision.reason = IsFresh(wgc) ? HealthReason::WgcFreshnessUnproven
                                   : HealthReason::NoFreshSource;
    decision.request_wgc_restart = wgc.has_value();
  }

  decision.switched_source = previous_source != decision.selected_source ||
                             previous_epoch != decision.selected_epoch;
  decision.revoke_recommendation =
      previous_source.has_value() && decision.switched_source;
  impl_->snapshot = decision;
  return decision;
}

CaptureHealthSnapshot DualSourceArbiter::Snapshot() const {
  std::lock_guard lock(impl_->mutex);
  return impl_->snapshot;
}

struct ForceRecognitionController::Impl final {
  explicit Impl(ForceRecognitionConfig initial_config)
      : config(std::move(initial_config)) {
    if (config.fresh_frame_budget == 0U) {
      config.fresh_frame_budget = 1U;
    }
    if (config.deadline < std::chrono::milliseconds::zero()) {
      config.deadline = std::chrono::milliseconds::zero();
    }
  }

  mutable std::mutex mutex{};
  ForceRecognitionConfig config{};
  bool primed_down{false};
  bool key_down{false};
  std::uint64_t poll_count{0U};
  std::uint64_t edge_count{0U};
  std::uint64_t request_id{0U};
  ForcePhase phase{ForcePhase::Idle};
  bool active{false};
  std::string source{};
  std::uint32_t fresh_frames_remaining{0U};
  std::uint32_t fresh_frames_seen{0U};
  std::uint32_t stale_frames_skipped{0U};
  std::uint64_t started_count{0U};
  std::uint64_t terminal_count{0U};
  std::optional<MonotonicTime> last_poll{};
  std::optional<MonotonicTime> last_edge{};
  std::optional<MonotonicTime> deadline_at{};
  std::optional<ForceTerminalReason> terminal_reason{};

  ForceRecognitionEvent Event(const ForceEventKind kind,
                              const MonotonicTime now) const {
    ForceRecognitionEvent event;
    event.kind = kind;
    event.request_id = request_id;
    event.source = source;
    event.monotonic_ms = MillisecondsSinceEpoch(now);
    event.fresh_frames_seen = fresh_frames_seen;
    event.stale_frames_skipped = stale_frames_skipped;
    return event;
  }

  ForceRecognitionEvent Finish(const ForceTerminalReason reason,
                               const MonotonicTime now) {
    auto event = Event(ForceEventKind::Terminal, now);
    event.terminal_reason = reason;
    active = false;
    phase = ForcePhase::Terminal;
    terminal_reason = reason;
    deadline_at.reset();
    ++terminal_count;
    return event;
  }

  void Expire(const MonotonicTime now,
              std::vector<ForceRecognitionEvent>& events) {
    if (!active || !deadline_at.has_value() || now < *deadline_at) {
      return;
    }
    events.push_back(Finish(fresh_frames_seen == 0U
                                ? ForceTerminalReason::NoFreshFrame
                                : ForceTerminalReason::Deadline,
                            now));
  }

  void Begin(std::string_view active_source, const MonotonicTime now,
             std::vector<ForceRecognitionEvent>& events) {
    if (active) {
      events.push_back(Finish(ForceTerminalReason::Superseded, now));
    }
    ++request_id;
    source.assign(active_source);
    active = true;
    phase = ForcePhase::Active;
    fresh_frames_remaining = config.fresh_frame_budget;
    fresh_frames_seen = 0U;
    stale_frames_skipped = 0U;
    terminal_reason.reset();
    deadline_at = now + config.deadline;
    ++started_count;
    events.push_back(Event(ForceEventKind::Started, now));
    if (!config.enabled) {
      events.push_back(Finish(ForceTerminalReason::Disabled, now));
    }
  }
};

ForceRecognitionController::ForceRecognitionController(
    ForceRecognitionConfig config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}
ForceRecognitionController::~ForceRecognitionController() = default;
ForceRecognitionController::ForceRecognitionController(
    ForceRecognitionController&&) noexcept = default;
ForceRecognitionController& ForceRecognitionController::operator=(
    ForceRecognitionController&&) noexcept = default;

void ForceRecognitionController::Prime(const bool key_down,
                                       const MonotonicTime now) {
  std::lock_guard lock(impl_->mutex);
  impl_->primed_down = key_down;
  impl_->key_down = key_down;
  impl_->last_poll = now;
}

std::vector<ForceRecognitionEvent> ForceRecognitionController::Poll(
    const bool key_down, const std::string_view active_source,
    const MonotonicTime now) {
  std::lock_guard lock(impl_->mutex);
  std::vector<ForceRecognitionEvent> events;
  impl_->Expire(now, events);
  ++impl_->poll_count;
  impl_->last_poll = now;
  const bool rising = key_down && !impl_->key_down;
  impl_->key_down = key_down;
  if (rising) {
    ++impl_->edge_count;
    impl_->last_edge = now;
    impl_->Begin(active_source, now, events);
  }
  return events;
}

std::vector<ForceRecognitionEvent> ForceRecognitionController::Request(
    const std::string_view active_source, const MonotonicTime now) {
  std::lock_guard lock(impl_->mutex);
  std::vector<ForceRecognitionEvent> events;
  impl_->Expire(now, events);
  impl_->Begin(active_source, now, events);
  return events;
}

std::vector<ForceRecognitionEvent>
ForceRecognitionController::ObserveFrame(const bool transport_fresh,
                                         const bool accepted_new_offer,
                                         const bool duplicate_current_content,
                                         const MonotonicTime now) {
  std::lock_guard lock(impl_->mutex);
  std::vector<ForceRecognitionEvent> events;
  impl_->Expire(now, events);
  if (!impl_->active) {
    return events;
  }
  if (!transport_fresh) {
    ++impl_->stale_frames_skipped;
    return events;
  }

  ++impl_->fresh_frames_seen;
  if (impl_->fresh_frames_remaining > 0U) {
    --impl_->fresh_frames_remaining;
  }
  if (accepted_new_offer) {
    events.push_back(impl_->Finish(ForceTerminalReason::Accepted, now));
  } else if (duplicate_current_content) {
    events.push_back(impl_->Finish(ForceTerminalReason::FreshDuplicate, now));
  } else if (impl_->fresh_frames_remaining == 0U) {
    events.push_back(
        impl_->Finish(ForceTerminalReason::FreshFrameBudgetExhausted, now));
  }
  return events;
}

std::vector<ForceRecognitionEvent> ForceRecognitionController::Tick(
    const MonotonicTime now) {
  std::lock_guard lock(impl_->mutex);
  std::vector<ForceRecognitionEvent> events;
  impl_->Expire(now, events);
  return events;
}

std::vector<ForceRecognitionEvent> ForceRecognitionController::SetEnabled(
    const bool enabled, const MonotonicTime now) {
  std::lock_guard lock(impl_->mutex);
  std::vector<ForceRecognitionEvent> events;
  impl_->config.enabled = enabled;
  if (!enabled && impl_->active) {
    events.push_back(impl_->Finish(ForceTerminalReason::Disabled, now));
  }
  return events;
}

std::vector<ForceRecognitionEvent> ForceRecognitionController::EndSession(
    const MonotonicTime now) {
  std::lock_guard lock(impl_->mutex);
  std::vector<ForceRecognitionEvent> events;
  if (impl_->active) {
    events.push_back(impl_->Finish(ForceTerminalReason::SessionEnd, now));
  }
  return events;
}

ForceRecognitionSnapshot ForceRecognitionController::Snapshot(
    const MonotonicTime now) const {
  std::lock_guard lock(impl_->mutex);
  ForceRecognitionSnapshot snapshot;
  snapshot.enabled = impl_->config.enabled;
  snapshot.primed_down = impl_->primed_down;
  snapshot.key_down = impl_->key_down;
  snapshot.poll_count = impl_->poll_count;
  snapshot.edge_count = impl_->edge_count;
  snapshot.request_id = impl_->request_id;
  snapshot.phase = impl_->phase;
  snapshot.source = impl_->source;
  snapshot.fresh_frames_remaining = impl_->fresh_frames_remaining;
  snapshot.fresh_frames_seen = impl_->fresh_frames_seen;
  snapshot.stale_frames_skipped = impl_->stale_frames_skipped;
  snapshot.started_count = impl_->started_count;
  snapshot.terminal_count = impl_->terminal_count;
  snapshot.terminal_reason = impl_->terminal_reason;
  if (impl_->last_poll.has_value()) {
    snapshot.last_poll_age_ms = NonNegativeAgeMs(now, *impl_->last_poll);
  }
  if (impl_->last_edge.has_value()) {
    snapshot.last_edge_age_ms = NonNegativeAgeMs(now, *impl_->last_edge);
  }
  if (impl_->active && impl_->deadline_at.has_value()) {
    snapshot.deadline_remaining_ms = std::max<std::int64_t>(
        0, std::chrono::duration_cast<std::chrono::milliseconds>(
               *impl_->deadline_at - now)
               .count());
  }
  return snapshot;
}

struct BoundedSamplePolicy::Impl final {
  explicit Impl(BoundedSampleConfig initial_config)
      : config(std::move(initial_config)) {}

  mutable std::mutex mutex{};
  BoundedSampleConfig config{};
  SampleBudgetSnapshot counters{};
  std::unordered_map<std::string, std::uint32_t> key_attempts{};

  void ResetCounters() {
    counters = SampleBudgetSnapshot{};
    counters.per_content_reason_budget = config.per_content_reason_budget;
    counters.session_budget = config.session_budget;
    counters.session_budget_remaining = config.session_budget;
  }
};

BoundedSamplePolicy::BoundedSamplePolicy(BoundedSampleConfig config)
    : impl_(std::make_unique<Impl>(std::move(config))) {
  impl_->ResetCounters();
}
BoundedSamplePolicy::~BoundedSamplePolicy() = default;
BoundedSamplePolicy::BoundedSamplePolicy(BoundedSamplePolicy&&) noexcept =
    default;
BoundedSamplePolicy& BoundedSamplePolicy::operator=(
    BoundedSamplePolicy&&) noexcept = default;

SampleDecision BoundedSamplePolicy::Evaluate(
    const SamplePolicyInput& input) {
  std::lock_guard lock(impl_->mutex);
  ++impl_->counters.evaluated;
  if (!input.transport_fresh) {
    ++impl_->counters.suppressed_stale;
    return SampleDecision::SuppressTransportStale;
  }
  if (input.trigger == SampleTrigger::Auto && !input.auto_eligible) {
    ++impl_->counters.suppressed_not_eligible;
    return SampleDecision::SuppressNotEligible;
  }
  if (input.source.empty() || input.content_key.empty() ||
      input.reason.empty()) {
    ++impl_->counters.suppressed_invalid_identity;
    return SampleDecision::SuppressInvalidIdentity;
  }
  if (impl_->counters.allowed >= impl_->config.session_budget) {
    ++impl_->counters.suppressed_session_budget;
    return SampleDecision::SuppressSessionBudget;
  }

  std::string key;
  key.reserve(input.source.size() + input.content_key.size() +
              input.reason.size() + 64U);
  key.append(input.source);
  key.push_back('\x1f');
  key.append(std::to_string(input.epoch));
  key.push_back('\x1f');
  key.append(input.content_key);
  key.push_back('\x1f');
  key.append(input.reason);
  key.push_back('\x1f');
  key.append(ToString(input.trigger));
  if (input.trigger != SampleTrigger::Auto) {
    key.push_back('\x1f');
    key.append(std::to_string(input.request_id));
  }

  auto& attempts = impl_->key_attempts[key];
  if (attempts >= impl_->config.per_content_reason_budget) {
    ++impl_->counters.suppressed_per_content_reason;
    return SampleDecision::SuppressPerContentReasonBudget;
  }
  ++attempts;
  ++impl_->counters.allowed;
  const auto remaining =
      static_cast<std::uint64_t>(impl_->config.session_budget) -
      impl_->counters.allowed;
  impl_->counters.session_budget_remaining = static_cast<std::uint32_t>(
      std::min<std::uint64_t>(remaining,
                              std::numeric_limits<std::uint32_t>::max()));
  return SampleDecision::Allow;
}

SampleBudgetSnapshot BoundedSamplePolicy::Snapshot() const {
  std::lock_guard lock(impl_->mutex);
  return impl_->counters;
}

void BoundedSamplePolicy::ResetSession() {
  std::lock_guard lock(impl_->mutex);
  impl_->key_attempts.clear();
  impl_->ResetCounters();
}

const char* ToString(const FrameFreshness value) noexcept {
  switch (value) {
    case FrameFreshness::Fresh:
      return "fresh";
    case FrameFreshness::FreshNewEpoch:
      return "fresh_new_epoch";
    case FrameFreshness::FreshSourceTimeMissing:
      return "fresh_source_time_missing";
    case FrameFreshness::DuplicateSourceTime:
      return "duplicate_source_time";
    case FrameFreshness::RegressedSourceTime:
      return "regressed_source_time";
    case FrameFreshness::DuplicateProgress:
      return "duplicate_progress";
    case FrameFreshness::RegressedProgress:
      return "regressed_progress";
    case FrameFreshness::RegressedEpoch:
      return "regressed_epoch";
    case FrameFreshness::InvalidSource:
      return "invalid_source";
  }
  return "invalid_source";
}

const char* ToString(const CaptureHealthState value) noexcept {
  switch (value) {
    case CaptureHealthState::Healthy:
      return "healthy";
    case CaptureHealthState::Suspect:
      return "suspect";
    case CaptureHealthState::DesktopFallback:
      return "desktop_fallback";
    case CaptureHealthState::Degraded:
      return "degraded";
  }
  return "degraded";
}

const char* ToString(const HealthReason value) noexcept {
  switch (value) {
    case HealthReason::WgcFresh:
      return "wgc_fresh";
    case HealthReason::WgcStalled:
      return "wgc_stalled";
    case HealthReason::DesktopProvedChange:
      return "desktop_proved_change";
    case HealthReason::DesktopAuthoritative:
      return "desktop_authoritative";
    case HealthReason::DesktopUnavailableWgcFresh:
      return "desktop_unavailable_wgc_fresh";
    case HealthReason::WgcFreshnessUnproven:
      return "wgc_freshness_unproven";
    case HealthReason::WgcUnavailable:
      return "wgc_unavailable";
    case HealthReason::WgcRecovering:
      return "wgc_recovering";
    case HealthReason::WgcRecoveredNewEpoch:
      return "wgc_recovered_new_epoch";
    case HealthReason::NoFreshSource:
      return "no_fresh_source";
    case HealthReason::TargetMinimized:
      return "target_minimized";
    case HealthReason::TargetHidden:
      return "target_hidden";
    case HealthReason::TargetOffscreen:
      return "target_offscreen";
    case HealthReason::TargetCrossOutput:
      return "target_cross_output";
    case HealthReason::TargetUnavailable:
      return "target_unavailable";
  }
  return "no_fresh_source";
}

const char* ToString(const ForcePhase value) noexcept {
  switch (value) {
    case ForcePhase::Idle:
      return "idle";
    case ForcePhase::Active:
      return "active";
    case ForcePhase::Terminal:
      return "terminal";
  }
  return "idle";
}

const char* ToString(const ForceEventKind value) noexcept {
  switch (value) {
    case ForceEventKind::Started:
      return "started";
    case ForceEventKind::Terminal:
      return "terminal";
  }
  return "started";
}

const char* ToString(const ForceTerminalReason value) noexcept {
  switch (value) {
    case ForceTerminalReason::Accepted:
      return "accepted";
    case ForceTerminalReason::FreshDuplicate:
      return "fresh_duplicate";
    case ForceTerminalReason::NoFreshFrame:
      return "no_fresh_frame";
    case ForceTerminalReason::Deadline:
      return "deadline";
    case ForceTerminalReason::FreshFrameBudgetExhausted:
      return "fresh_frame_budget_exhausted";
    case ForceTerminalReason::Disabled:
      return "disabled";
    case ForceTerminalReason::SessionEnd:
      return "session_end";
    case ForceTerminalReason::Superseded:
      return "superseded";
  }
  return "deadline";
}

const char* ToString(const SampleTrigger value) noexcept {
  switch (value) {
    case SampleTrigger::Auto:
      return "auto";
    case SampleTrigger::ManualF8:
      return "manual_f8";
    case SampleTrigger::ForceF9:
      return "force_f9";
  }
  return "auto";
}

const char* ToString(const SampleDecision value) noexcept {
  switch (value) {
    case SampleDecision::Allow:
      return "allow";
    case SampleDecision::SuppressTransportStale:
      return "suppress_transport_stale";
    case SampleDecision::SuppressNotEligible:
      return "suppress_not_eligible";
    case SampleDecision::SuppressInvalidIdentity:
      return "suppress_invalid_identity";
    case SampleDecision::SuppressPerContentReasonBudget:
      return "suppress_per_content_reason_budget";
    case SampleDecision::SuppressSessionBudget:
      return "suppress_session_budget";
  }
  return "suppress_invalid_identity";
}

}  // namespace lol_assistant::app
