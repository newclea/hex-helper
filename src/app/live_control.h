#pragma once

#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace lol_assistant::app {

using MonotonicTime = std::chrono::steady_clock::time_point;

// Backend-neutral transport identity. source_time is the producer's monotonic
// clock; progress is an epoch-local frame/callback sequence and is only used as
// degraded freshness evidence when source_time is unavailable.
struct FrameTransportInput final {
  std::string source{};
  std::uint64_t epoch{0U};
  std::optional<std::int64_t> source_time{};
  std::string content_hash{};
  std::uint64_t progress{0U};
  MonotonicTime observed_at{};
};

enum class FrameFreshness {
  Fresh,
  FreshNewEpoch,
  FreshSourceTimeMissing,
  DuplicateSourceTime,
  RegressedSourceTime,
  DuplicateProgress,
  RegressedProgress,
  RegressedEpoch,
  InvalidSource,
};

struct FrameFreshnessObservation final {
  FrameTransportInput input{};
  FrameFreshness freshness{FrameFreshness::InvalidSource};
  bool process_allowed{false};
  bool content_changed{false};
  std::uint64_t accepted_sequence{0U};
  std::uint64_t source_accepted_frames{0U};
  std::uint64_t source_rejected_frames{0U};
};

struct FrameFreshnessSnapshot final {
  std::string source{};
  std::uint64_t epoch{0U};
  std::optional<std::int64_t> source_time{};
  std::string content_hash{};
  std::uint64_t progress{0U};
  FrameFreshness last_freshness{FrameFreshness::InvalidSource};
  std::uint64_t accepted_frames{0U};
  std::uint64_t rejected_frames{0U};
  std::optional<std::int64_t> last_fresh_age_ms{};
};

// Thread safety: Observe and Snapshot are internally serialized. The gate owns
// independent state per source string and has no capture/backend dependency.
class FrameFreshnessGate final {
 public:
  FrameFreshnessGate();
  ~FrameFreshnessGate();
  FrameFreshnessGate(FrameFreshnessGate&&) noexcept;
  FrameFreshnessGate& operator=(FrameFreshnessGate&&) noexcept;
  FrameFreshnessGate(const FrameFreshnessGate&) = delete;
  FrameFreshnessGate& operator=(const FrameFreshnessGate&) = delete;

  [[nodiscard]] FrameFreshnessObservation Observe(
      const FrameTransportInput& input);
  [[nodiscard]] std::optional<FrameFreshnessSnapshot> Snapshot(
      std::string_view source, MonotonicTime now) const;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

enum class CaptureHealthState {
  Healthy,
  Suspect,
  DesktopFallback,
  Degraded,
};

enum class HealthReason {
  WgcFresh,
  WgcStalled,
  DesktopProvedChange,
  DesktopAuthoritative,
  DesktopUnavailableWgcFresh,
  WgcFreshnessUnproven,
  WgcUnavailable,
  WgcRecovering,
  WgcRecoveredNewEpoch,
  NoFreshSource,
  TargetMinimized,
  TargetHidden,
  TargetOffscreen,
  TargetCrossOutput,
  TargetUnavailable,
};

struct CaptureHealthSnapshot final {
  CaptureHealthState state{CaptureHealthState::Suspect};
  HealthReason reason{HealthReason::NoFreshSource};
  std::optional<std::string> selected_source{};
  std::optional<std::uint64_t> selected_epoch{};
  bool revoke_recommendation{false};
  bool request_wgc_restart{false};
  bool switched_source{false};
};

using HealthDecision = CaptureHealthSnapshot;

// Thread safety: decisions are internally serialized. Inputs are role-based
// DTOs; the first argument is WGC and the second is desktop duplication.
class DualSourceArbiter final {
 public:
  DualSourceArbiter();
  ~DualSourceArbiter();
  DualSourceArbiter(DualSourceArbiter&&) noexcept;
  DualSourceArbiter& operator=(DualSourceArbiter&&) noexcept;
  DualSourceArbiter(const DualSourceArbiter&) = delete;
  DualSourceArbiter& operator=(const DualSourceArbiter&) = delete;

  [[nodiscard]] HealthDecision Decide(
      const std::optional<FrameFreshnessObservation>& wgc,
      const std::optional<FrameFreshnessObservation>& desktop);
  [[nodiscard]] HealthDecision Decide(
      const FrameFreshnessObservation& wgc,
      const FrameFreshnessObservation& desktop);
  [[nodiscard]] CaptureHealthSnapshot Snapshot() const;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

enum class ForcePhase { Idle, Active, Terminal };
enum class ForceEventKind { Started, Terminal };
enum class ForceTerminalReason {
  Accepted,
  FreshDuplicate,
  NoFreshFrame,
  Deadline,
  FreshFrameBudgetExhausted,
  Disabled,
  SessionEnd,
  Superseded,
};

struct ForceRecognitionConfig final {
  bool enabled{true};
  std::uint32_t fresh_frame_budget{5U};
  std::chrono::milliseconds deadline{1'500};
};

struct ForceRecognitionEvent final {
  ForceEventKind kind{ForceEventKind::Started};
  std::uint64_t request_id{0U};
  std::string source{};
  std::int64_t monotonic_ms{0};
  std::uint32_t fresh_frames_seen{0U};
  std::uint32_t stale_frames_skipped{0U};
  std::optional<ForceTerminalReason> terminal_reason{};
};

struct ForceRecognitionSnapshot final {
  bool enabled{true};
  bool primed_down{false};
  bool key_down{false};
  std::uint64_t poll_count{0U};
  std::uint64_t edge_count{0U};
  std::uint64_t request_id{0U};
  ForcePhase phase{ForcePhase::Idle};
  std::string source{};
  std::uint32_t fresh_frames_remaining{0U};
  std::uint32_t fresh_frames_seen{0U};
  std::uint32_t stale_frames_skipped{0U};
  std::uint64_t started_count{0U};
  std::uint64_t terminal_count{0U};
  std::optional<std::int64_t> last_poll_age_ms{};
  std::optional<std::int64_t> last_edge_age_ms{};
  std::optional<std::int64_t> deadline_remaining_ms{};
  std::optional<ForceTerminalReason> terminal_reason{};
};

// Thread safety: every public method is internally serialized. Time and key
// state are supplied by the caller, enabling deterministic fake-clock tests and
// keeping Win32 input acquisition outside this primitive.
class ForceRecognitionController final {
 public:
  explicit ForceRecognitionController(
      ForceRecognitionConfig config = ForceRecognitionConfig{});
  ~ForceRecognitionController();
  ForceRecognitionController(ForceRecognitionController&&) noexcept;
  ForceRecognitionController& operator=(ForceRecognitionController&&) noexcept;
  ForceRecognitionController(const ForceRecognitionController&) = delete;
  ForceRecognitionController& operator=(const ForceRecognitionController&) =
      delete;

  void Prime(bool key_down, MonotonicTime now);
  [[nodiscard]] std::vector<ForceRecognitionEvent> Poll(
      bool key_down, std::string_view active_source, MonotonicTime now);
  [[nodiscard]] std::vector<ForceRecognitionEvent> Request(
      std::string_view active_source, MonotonicTime now);
  [[nodiscard]] std::vector<ForceRecognitionEvent> ObserveFrame(
      bool transport_fresh, bool accepted_new_offer,
      bool duplicate_current_content, MonotonicTime now);
  [[nodiscard]] std::vector<ForceRecognitionEvent> Tick(MonotonicTime now);
  [[nodiscard]] std::vector<ForceRecognitionEvent> SetEnabled(
      bool enabled, MonotonicTime now);
  [[nodiscard]] std::vector<ForceRecognitionEvent> EndSession(
      MonotonicTime now);
  [[nodiscard]] ForceRecognitionSnapshot Snapshot(MonotonicTime now) const;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

enum class SampleTrigger { Auto, ManualF8, ForceF9 };
enum class SampleDecision {
  Allow,
  SuppressTransportStale,
  SuppressNotEligible,
  SuppressInvalidIdentity,
  SuppressPerContentReasonBudget,
  SuppressSessionBudget,
};

struct BoundedSampleConfig final {
  std::uint32_t per_content_reason_budget{1U};
  std::uint32_t session_budget{20U};
};

struct SamplePolicyInput final {
  SampleTrigger trigger{SampleTrigger::Auto};
  bool transport_fresh{false};
  std::string source{};
  std::uint64_t epoch{0U};
  std::string content_key{};
  std::string reason{};
  bool auto_eligible{false};
  std::uint64_t request_id{0U};
};

struct SampleBudgetSnapshot final {
  std::uint64_t evaluated{0U};
  std::uint64_t allowed{0U};
  std::uint64_t suppressed_stale{0U};
  std::uint64_t suppressed_not_eligible{0U};
  std::uint64_t suppressed_invalid_identity{0U};
  std::uint64_t suppressed_per_content_reason{0U};
  std::uint64_t suppressed_session_budget{0U};
  std::uint32_t per_content_reason_budget{1U};
  std::uint32_t session_budget{20U};
  std::uint32_t session_budget_remaining{20U};
};

// Thread safety: Evaluate is an atomic check-and-consume operation. Budgets are
// charged on Allow (collector attempt), never on stale/suppressed observations.
class BoundedSamplePolicy final {
 public:
  explicit BoundedSamplePolicy(
      BoundedSampleConfig config = BoundedSampleConfig{});
  ~BoundedSamplePolicy();
  BoundedSamplePolicy(BoundedSamplePolicy&&) noexcept;
  BoundedSamplePolicy& operator=(BoundedSamplePolicy&&) noexcept;
  BoundedSamplePolicy(const BoundedSamplePolicy&) = delete;
  BoundedSamplePolicy& operator=(const BoundedSamplePolicy&) = delete;

  [[nodiscard]] SampleDecision Evaluate(const SamplePolicyInput& input);
  [[nodiscard]] SampleBudgetSnapshot Snapshot() const;
  void ResetSession();

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

[[nodiscard]] const char* ToString(FrameFreshness value) noexcept;
[[nodiscard]] const char* ToString(CaptureHealthState value) noexcept;
[[nodiscard]] const char* ToString(HealthReason value) noexcept;
[[nodiscard]] const char* ToString(ForcePhase value) noexcept;
[[nodiscard]] const char* ToString(ForceEventKind value) noexcept;
[[nodiscard]] const char* ToString(ForceTerminalReason value) noexcept;
[[nodiscard]] const char* ToString(SampleTrigger value) noexcept;
[[nodiscard]] const char* ToString(SampleDecision value) noexcept;

}  // namespace lol_assistant::app
