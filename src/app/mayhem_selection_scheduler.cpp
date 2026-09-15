#include "mayhem_selection_scheduler.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>

namespace lol_assistant::app {
namespace {

constexpr std::array<std::uint32_t, 4U> kTriggerLevels{1U, 7U, 11U, 15U};

[[nodiscard]] std::chrono::milliseconds
TriggerWindow(const live_client::LiveClientPlayerState &player,
              const MayhemSelectionSchedulerConfig &config) noexcept {
  const auto respawn_ms = std::chrono::milliseconds{static_cast<std::int64_t>(
      std::ceil(std::max(0.0, player.respawn_timer_seconds) * 1'000.0))};
  return std::clamp(respawn_ms + config.post_respawn_grace,
                    config.minimum_window, config.maximum_window);
}

} // namespace

MayhemSelectionScheduler::MayhemSelectionScheduler(
    const std::uint32_t completed_offers, MayhemSelectionSchedulerConfig config)
    : config_(config), completed_offers_(completed_offers) {
  if (completed_offers_ > 4U) {
    throw std::invalid_argument("completed offers must be in [0, 4]");
  }
  if (config_.minimum_window <= std::chrono::milliseconds::zero() ||
      config_.post_respawn_grace < std::chrono::milliseconds::zero() ||
      config_.fountain_after_respawn < std::chrono::milliseconds::zero() ||
      config_.maximum_window < config_.minimum_window) {
    throw std::invalid_argument("invalid Mayhem selection scheduler timing");
  }
  ResetForNextStage();
}

std::optional<std::uint32_t>
MayhemSelectionScheduler::NextStage() const noexcept {
  if (completed_offers_ >= 4U) {
    return std::nullopt;
  }
  return completed_offers_ + 1U;
}

std::uint32_t MayhemSelectionScheduler::EligibleOfferCount() const noexcept {
  if (!last_player_.has_value()) {
    return 0U;
  }
  const std::uint32_t level = last_player_->level;
  if (level >= 15U) {
    return 4U;
  }
  if (level >= 11U) {
    return 3U;
  }
  if (level >= 7U) {
    return 2U;
  }
  if (level >= 1U) {
    return 1U;
  }
  return 0U;
}

std::uint32_t MayhemSelectionScheduler::PendingCount() const noexcept {
  const auto eligible = EligibleOfferCount();
  return eligible > completed_offers_ ? eligible - completed_offers_ : 0U;
}

bool MayhemSelectionScheduler::HasQueuedEligibleOffer() const noexcept {
  return completed_offers_ < 4U && completed_offers_ < EligibleOfferCount();
}

std::optional<std::uint32_t>
MayhemSelectionScheduler::ThresholdLevel() const noexcept {
  if (completed_offers_ >= 4U) {
    return std::nullopt;
  }
  return kTriggerLevels[completed_offers_];
}

void MayhemSelectionScheduler::ResetForNextStage() noexcept {
  active_until_.reset();
  hard_active_until_.reset();
  if (completed_offers_ >= 4U) {
    phase_ = MayhemSelectionPhase::Complete;
    requires_alive_before_trigger_ = false;
  } else if (completed_offers_ == 0U) {
    phase_ = MayhemSelectionPhase::WaitingInitialOffer;
  } else {
    phase_ = MayhemSelectionPhase::WaitingLevel;
  }
}

void MayhemSelectionScheduler::Expire(
    const std::chrono::steady_clock::time_point now,
    std::vector<MayhemSelectionEvent> &events) {
  static_cast<void>(now);
  static_cast<void>(events);
}

void MayhemSelectionScheduler::ArmIfEligible(
    const std::chrono::steady_clock::time_point now,
    std::vector<MayhemSelectionEvent> &events) {
  static_cast<void>(now);
  if (!last_player_.has_value() || completed_offers_ >= 4U ||
      phase_ == MayhemSelectionPhase::Armed ||
      phase_ == MayhemSelectionPhase::Triggered) {
    return;
  }
  const auto threshold = ThresholdLevel();
  if (!threshold.has_value() || last_player_->level < *threshold) {
    phase_ = MayhemSelectionPhase::WaitingLevel;
    return;
  }
  phase_ = MayhemSelectionPhase::Armed;
  const auto stage = NextStage();
  if (stage.has_value()) {
    auto event = MayhemSelectionEvent{
        MayhemSelectionEventKind::Armed, *stage, threshold,
        completed_offers_ == 0U
            ? "game_start_ready_for_initial_offer"
            : "level_threshold_reached_waiting_for_fountain_or_death",
        last_player_};
    event.snapshot = Snapshot(now);
    events.push_back(std::move(event));
  }
}

std::vector<MayhemSelectionEvent> MayhemSelectionScheduler::Observe(
    const live_client::LiveClientSnapshot &snapshot,
    const std::chrono::steady_clock::time_point now) {
  std::vector<MayhemSelectionEvent> events;
  Expire(now, events);
  if (snapshot.status != live_client::LiveClientStatus::Ready ||
      !snapshot.player.has_value()) {
    return events;
  }

  const auto prior_dead = previous_dead_;
  last_player_ = snapshot.player;
  if (prior_dead.has_value() && *prior_dead && !last_player_->is_dead &&
      (phase_ == MayhemSelectionPhase::Armed ||
       phase_ == MayhemSelectionPhase::Triggered)) {
    fountain_until_ = now + config_.fountain_after_respawn;
  }
  ArmIfEligible(now, events);
  const bool in_fountain =
      last_player_->is_dead ||
      (fountain_until_.has_value() && now < *fountain_until_);

  if (requires_alive_before_trigger_) {
    if (!last_player_->is_dead) {
      requires_alive_before_trigger_ = false;
    }
    previous_dead_ = last_player_->is_dead;
    return events;
  }

  const bool death_edge =
      last_player_->is_dead && (!prior_dead.has_value() || !*prior_dead);
  const bool open_initial_offer =
      completed_offers_ == 0U && !initial_offer_opened_;
  const bool pending = PendingCount() > 0U;
  const bool can_trigger =
      pending && (open_initial_offer || death_edge || in_fountain);
  if (phase_ == MayhemSelectionPhase::Armed && pending) {
    phase_ = MayhemSelectionPhase::Triggered;
    if (open_initial_offer) {
      initial_offer_opened_ = true;
    }
    active_until_ = now + TriggerWindow(*last_player_, config_);
    hard_active_until_ = now + config_.maximum_window;
    const auto stage = NextStage();
    if (stage.has_value() && can_trigger) {
      const bool initial_offer = open_initial_offer;
      auto event = MayhemSelectionEvent{
          initial_offer || !death_edge
              ? MayhemSelectionEventKind::FountainTriggered
              : MayhemSelectionEventKind::DeathTriggered,
          *stage, ThresholdLevel(),
          initial_offer
              ? "game_start_initial_offer"
              : (death_edge ? (prior_dead.has_value()
                                   ? "first_death_after_level_threshold"
                                   : "cold_start_dead_after_level_threshold")
                            : "fountain_or_dead_after_level_threshold"),
          last_player_};
      event.snapshot = Snapshot(now);
      events.push_back(std::move(event));
    }
  } else if (phase_ == MayhemSelectionPhase::Triggered && death_edge &&
             pending) {
    const auto stage = NextStage();
    if (stage.has_value()) {
      auto event = MayhemSelectionEvent{
          MayhemSelectionEventKind::DeathTriggered, *stage, ThresholdLevel(),
          "death_reread_while_pending_click", last_player_};
      event.snapshot = Snapshot(now);
      events.push_back(std::move(event));
    }
  } else if (phase_ == MayhemSelectionPhase::Triggered &&
             last_player_->is_dead && active_until_.has_value() &&
             hard_active_until_.has_value()) {
    active_until_ = std::min(
        *hard_active_until_,
        std::max(*active_until_, now + TriggerWindow(*last_player_, config_)));
  }
  previous_dead_ = last_player_->is_dead;
  return events;
}

std::vector<MayhemSelectionEvent> MayhemSelectionScheduler::OfferDetected(
    const std::chrono::steady_clock::time_point now) {
  std::vector<MayhemSelectionEvent> events;
  if (completed_offers_ >= 4U) {
    return events;
  }
  if (PendingCount() > 0U) {
    phase_ = MayhemSelectionPhase::Triggered;
    requires_alive_before_trigger_ = false;
    if (last_player_.has_value()) {
      active_until_ = now + TriggerWindow(*last_player_, config_);
    } else {
      active_until_ = now + config_.minimum_window;
    }
    hard_active_until_ = now + config_.maximum_window;
  }
  auto detected = MayhemSelectionEvent{
      MayhemSelectionEventKind::OfferDetected, completed_offers_ + 1U,
      ThresholdLevel(), "three_card_offer_recognized_waiting_for_click",
      last_player_};
  detected.snapshot = Snapshot(now);
  events.push_back(std::move(detected));
  return events;
}

std::vector<MayhemSelectionEvent> MayhemSelectionScheduler::SelectionConfirmed(
    const std::chrono::steady_clock::time_point now) {
  std::vector<MayhemSelectionEvent> events;
  if (completed_offers_ >= 4U) {
    return events;
  }
  const std::uint32_t confirmed_stage = completed_offers_ + 1U;
  const auto confirmed_threshold = ThresholdLevel();
  ++completed_offers_;

  if (HasQueuedEligibleOffer()) {
    phase_ = MayhemSelectionPhase::Triggered;
    requires_alive_before_trigger_ = false;
    if (last_player_.has_value()) {
      active_until_ = now + TriggerWindow(*last_player_, config_);
    } else {
      active_until_ = now + config_.minimum_window;
    }
    hard_active_until_ = now + config_.maximum_window;
    auto confirmed = MayhemSelectionEvent{
        MayhemSelectionEventKind::SelectionConfirmed, confirmed_stage,
        confirmed_threshold, "click_glow_confirmed_pending_decremented",
        last_player_};
    confirmed.snapshot = Snapshot(now);
    events.push_back(std::move(confirmed));
    const auto next_stage = NextStage();
    auto queued = MayhemSelectionEvent{
        MayhemSelectionEventKind::QueuedOfferTriggered,
        next_stage.value_or(confirmed_stage), ThresholdLevel(),
        "eligible_offer_still_waiting_for_click_glow", last_player_};
    queued.snapshot = Snapshot(now);
    events.push_back(std::move(queued));
    return events;
  }

  requires_alive_before_trigger_ = false;
  ResetForNextStage();
  ArmIfEligible(now, events);
  auto confirmed = MayhemSelectionEvent{
      MayhemSelectionEventKind::SelectionConfirmed, confirmed_stage,
      confirmed_threshold, "click_glow_confirmed_pending_decremented",
      last_player_};
  confirmed.snapshot = Snapshot(now);
  events.insert(events.begin(), std::move(confirmed));
  return events;
}

bool MayhemSelectionScheduler::HighFrequencyActive(
    const std::chrono::steady_clock::time_point now) const noexcept {
  static_cast<void>(now);
  return PendingCount() > 0U;
}

MayhemSelectionSnapshot MayhemSelectionScheduler::Snapshot(
    const std::chrono::steady_clock::time_point now) const noexcept {
  MayhemSelectionSnapshot snapshot;
  snapshot.phase = phase_;
  snapshot.completed_offers = completed_offers_;
  snapshot.pending_count = PendingCount();
  snapshot.next_stage = NextStage();
  snapshot.threshold_level = ThresholdLevel();
  snapshot.high_frequency_active = HighFrequencyActive(now);
  snapshot.requires_alive_before_trigger = requires_alive_before_trigger_;
  return snapshot;
}

const char *ToString(const MayhemSelectionPhase phase) noexcept {
  switch (phase) {
  case MayhemSelectionPhase::WaitingInitialOffer:
    return "WAITING_INITIAL_OFFER";
  case MayhemSelectionPhase::WaitingLevel:
    return "WAITING_LEVEL";
  case MayhemSelectionPhase::Armed:
    return "ARMED";
  case MayhemSelectionPhase::Triggered:
    return "TRIGGERED";
  case MayhemSelectionPhase::Complete:
    return "COMPLETE";
  default:
    return "WAITING_LEVEL";
  }
}

const char *ToString(const MayhemSelectionEventKind kind) noexcept {
  switch (kind) {
  case MayhemSelectionEventKind::Armed:
    return "ARMED";
  case MayhemSelectionEventKind::DeathTriggered:
    return "DEATH_TRIGGERED";
  case MayhemSelectionEventKind::FountainTriggered:
    return "FOUNTAIN_TRIGGERED";
  case MayhemSelectionEventKind::QueuedOfferTriggered:
    return "QUEUED_OFFER_TRIGGERED";
  case MayhemSelectionEventKind::OfferDetected:
    return "OFFER_DETECTED";
  case MayhemSelectionEventKind::SelectionConfirmed:
    return "SELECTION_CONFIRMED";
  case MayhemSelectionEventKind::WindowExpired:
    return "WINDOW_EXPIRED";
  default:
    return "ARMED";
  }
}

} // namespace lol_assistant::app
