#pragma once

#include <chrono>
#include <cstdint>
#include <optional>
#include <string_view>
#include <vector>

#include "lol_assistant/live_client/live_client_data.h"

namespace lol_assistant::app {

enum class MayhemSelectionPhase {
  WaitingInitialOffer,
  WaitingLevel,
  Armed,
  Triggered,
  Complete,
};

enum class MayhemSelectionEventKind {
  Armed,
  DeathTriggered,
  FountainTriggered,
  QueuedOfferTriggered,
  OfferDetected,
  SelectionConfirmed,
  WindowExpired,
};

struct MayhemSelectionSnapshot final {
  MayhemSelectionPhase phase{MayhemSelectionPhase::WaitingLevel};
  std::uint32_t completed_offers{0U};
  std::uint32_t pending_count{0U};
  std::optional<std::uint32_t> next_stage{1U};
  std::optional<std::uint32_t> threshold_level{};
  bool high_frequency_active{false};
  bool requires_alive_before_trigger{false};
};

struct MayhemSelectionEvent final {
  MayhemSelectionEventKind kind{MayhemSelectionEventKind::Armed};
  std::uint32_t stage{1U};
  std::optional<std::uint32_t> threshold_level{};
  std::string_view reason{};
  std::optional<live_client::LiveClientPlayerState> player{};
  // Snapshot captured at this exact state transition. Consumers must not pair
  // a batch of ordered events with one final scheduler snapshot.
  MayhemSelectionSnapshot snapshot{};
};

struct MayhemSelectionSchedulerConfig final {
  std::chrono::milliseconds minimum_window{8'000};
  std::chrono::milliseconds post_respawn_grace{5'000};
  std::chrono::milliseconds maximum_window{60'000};
  std::chrono::milliseconds fountain_after_respawn{12'000};
};

// Hexcore pick windows: first offer at game start, then levels 7/11/15.
// Pending increments at those thresholds and decrements only after a
// left-click gold-glow confirm. Until that confirm, recognition stays
// armed at any level. The class only schedules capture; it never sends
// input.
class MayhemSelectionScheduler final {
public:
  explicit MayhemSelectionScheduler(std::uint32_t completed_offers = 0U,
                                    MayhemSelectionSchedulerConfig config = {});

  [[nodiscard]] std::vector<MayhemSelectionEvent>
  Observe(const live_client::LiveClientSnapshot &snapshot,
          std::chrono::steady_clock::time_point now);
  [[nodiscard]] std::vector<MayhemSelectionEvent>
  OfferDetected(std::chrono::steady_clock::time_point now);
  [[nodiscard]] std::vector<MayhemSelectionEvent>
  SelectionConfirmed(std::chrono::steady_clock::time_point now);
  [[nodiscard]] MayhemSelectionSnapshot
  Snapshot(std::chrono::steady_clock::time_point now) const noexcept;
  [[nodiscard]] bool
  HighFrequencyActive(std::chrono::steady_clock::time_point now) const noexcept;

private:
  [[nodiscard]] std::optional<std::uint32_t> NextStage() const noexcept;
  [[nodiscard]] std::uint32_t EligibleOfferCount() const noexcept;
  [[nodiscard]] std::uint32_t PendingCount() const noexcept;
  [[nodiscard]] bool HasQueuedEligibleOffer() const noexcept;
  [[nodiscard]] std::optional<std::uint32_t> ThresholdLevel() const noexcept;
  void ResetForNextStage() noexcept;
  void Expire(std::chrono::steady_clock::time_point now,
              std::vector<MayhemSelectionEvent> &events);
  void ArmIfEligible(std::chrono::steady_clock::time_point now,
                     std::vector<MayhemSelectionEvent> &events);

  MayhemSelectionSchedulerConfig config_{};
  std::uint32_t completed_offers_{0U};
  MayhemSelectionPhase phase_{MayhemSelectionPhase::WaitingLevel};
  std::optional<live_client::LiveClientPlayerState> last_player_{};
  std::optional<bool> previous_dead_{};
  std::optional<std::chrono::steady_clock::time_point> active_until_{};
  std::optional<std::chrono::steady_clock::time_point> hard_active_until_{};
  std::optional<std::chrono::steady_clock::time_point> fountain_until_{};
  bool requires_alive_before_trigger_{false};
  bool initial_offer_opened_{false};
};

[[nodiscard]] const char *ToString(MayhemSelectionPhase phase) noexcept;
[[nodiscard]] const char *ToString(MayhemSelectionEventKind kind) noexcept;

} // namespace lol_assistant::app
