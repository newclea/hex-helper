#include <chrono>
#include <iostream>
#include <stdexcept>
#include <string>

#include "mayhem_selection_scheduler.h"

namespace {

using lol_assistant::app::MayhemSelectionEventKind;
using lol_assistant::app::MayhemSelectionPhase;
using lol_assistant::app::MayhemSelectionScheduler;
using lol_assistant::live_client::LiveClientPlayerState;
using lol_assistant::live_client::LiveClientSnapshot;
using lol_assistant::live_client::LiveClientStatus;

void Require(const bool condition, const std::string &message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

[[nodiscard]] LiveClientSnapshot
Player(const std::uint32_t level, const bool dead, const double respawn = 0.0) {
  LiveClientSnapshot snapshot;
  snapshot.status = LiveClientStatus::Ready;
  snapshot.reason = "ok";
  snapshot.player = LiveClientPlayerState{"Varus", level, dead, respawn};
  return snapshot;
}

void TestFirstOfferOpensAtGameStart() {
  const auto start = std::chrono::steady_clock::time_point{};
  MayhemSelectionScheduler scheduler;
  Require(scheduler.Snapshot(start).phase ==
                  MayhemSelectionPhase::WaitingInitialOffer &&
              scheduler.Snapshot(start).pending_count == 0U &&
              scheduler.Snapshot(start).threshold_level == 1U,
          "before Live Client, the first offer is waiting at level 1");

  const auto opened = scheduler.Observe(Player(1U, false), start);
  Require(opened.size() == 2U &&
              opened[0].kind == MayhemSelectionEventKind::Armed &&
              opened[0].stage == 1U && opened[0].threshold_level == 1U &&
              opened[0].snapshot.pending_count == 1U &&
              opened[1].kind == MayhemSelectionEventKind::FountainTriggered &&
              opened[1].reason == "game_start_initial_offer" &&
              scheduler.HighFrequencyActive(start) &&
              scheduler.Snapshot(start).pending_count == 1U,
          "the first live player snapshot must open the game-start pick window");

  const auto later_death =
      scheduler.Observe(Player(5U, true, 8.0), start + std::chrono::seconds{1});
  Require(later_death.size() == 1U &&
              later_death[0].kind == MayhemSelectionEventKind::DeathTriggered &&
              later_death[0].stage == 1U &&
              scheduler.HighFrequencyActive(start) &&
              scheduler.Snapshot(start).pending_count == 1U,
          "a later death must reread the same pending offer, not invent a stage");
}

void TestOcrDoesNotConsumePendingUntilClickGlow() {
  const auto start = std::chrono::steady_clock::time_point{};
  MayhemSelectionScheduler scheduler;
  static_cast<void>(scheduler.Observe(Player(5U, false), start));
  const auto detected = scheduler.OfferDetected(start);
  Require(detected.size() == 1U &&
              detected[0].kind == MayhemSelectionEventKind::OfferDetected &&
              scheduler.Snapshot(start).completed_offers == 0U &&
              scheduler.Snapshot(start).pending_count == 1U &&
              scheduler.HighFrequencyActive(start),
          "reading the three names must keep waiting for a click-glow confirm");

  Require(scheduler.Observe(Player(6U, false), start).empty() &&
              scheduler.HighFrequencyActive(start) &&
              scheduler.Snapshot(start).pending_count == 1U,
          "without a click glow, any later level must keep waiting");
}

void TestClickGlowConsumesPendingAndLevelSixStaysIdle() {
  const auto start = std::chrono::steady_clock::time_point{};
  MayhemSelectionScheduler scheduler;
  static_cast<void>(scheduler.Observe(Player(5U, false), start));
  static_cast<void>(scheduler.OfferDetected(start));
  const auto confirmed = scheduler.SelectionConfirmed(start);
  Require(confirmed.size() == 1U &&
              confirmed[0].kind ==
                  MayhemSelectionEventKind::SelectionConfirmed &&
              scheduler.Snapshot(start).completed_offers == 1U &&
              scheduler.Snapshot(start).pending_count == 0U &&
              scheduler.Snapshot(start).phase ==
                  MayhemSelectionPhase::WaitingLevel,
          "left-click gold glow must decrement the pending counter");

  Require(scheduler.Observe(Player(6U, false), start).empty() &&
              scheduler.Observe(Player(6U, true, 10.0), start).empty() &&
              !scheduler.HighFrequencyActive(start) &&
              scheduler.Snapshot(start).pending_count == 0U,
          "after a glow confirm, level six must not open the next offer");
}

void TestLevelSevenKeepsWaitingForClickWhileAlive() {
  const auto start = std::chrono::steady_clock::time_point{};
  MayhemSelectionScheduler scheduler;
  static_cast<void>(scheduler.Observe(Player(5U, false), start));
  static_cast<void>(scheduler.SelectionConfirmed(start));

  const auto armed = scheduler.Observe(Player(7U, false), start);
  Require(armed.size() == 1U &&
              armed[0].kind == MayhemSelectionEventKind::Armed &&
              armed[0].stage == 2U && armed[0].threshold_level == 7U &&
              armed[0].snapshot.pending_count == 1U &&
              scheduler.HighFrequencyActive(start),
          "level seven must keep scanning and wait for a click at any state");

  const auto trigger = scheduler.Observe(Player(7U, true, 12.0),
                                         start + std::chrono::seconds{1});
  Require(trigger.size() == 1U &&
              trigger[0].kind == MayhemSelectionEventKind::DeathTriggered &&
              trigger[0].stage == 2U &&
              scheduler.HighFrequencyActive(start + std::chrono::seconds{1}),
          "death while pending must force another recognition pass");
}

void TestFountainAfterRespawnStillWaitsIfUnconfirmed() {
  const auto start = std::chrono::steady_clock::time_point{};
  MayhemSelectionScheduler scheduler;
  static_cast<void>(scheduler.Observe(Player(1U, false), start));
  static_cast<void>(scheduler.Observe(Player(1U, true, 8.0), start));
  const auto detected = scheduler.OfferDetected(start);
  Require(detected.size() == 1U &&
              detected[0].kind == MayhemSelectionEventKind::OfferDetected &&
              scheduler.Snapshot(start).pending_count == 1U &&
              scheduler.HighFrequencyActive(start),
          "OCR while dead must not consume the pending click");

  const auto confirmed = scheduler.SelectionConfirmed(start);
  Require(confirmed[0].kind == MayhemSelectionEventKind::SelectionConfirmed &&
              scheduler.Snapshot(start).pending_count == 0U,
          "glow confirm while dead consumes only the current pending offer");

  const auto armed_next = scheduler.Observe(Player(7U, true, 4.0), start);
  Require(armed_next.size() == 2U &&
              armed_next[0].kind == MayhemSelectionEventKind::Armed &&
              armed_next[1].kind == MayhemSelectionEventKind::FountainTriggered &&
              scheduler.HighFrequencyActive(start),
          "a later eligible death must open the next pending click wait");
}

void TestQueuedEligibleRoundStaysHotOnSameDeath() {
  const auto start = std::chrono::steady_clock::time_point{};
  MayhemSelectionScheduler scheduler{1U};
  const auto cold_start = scheduler.Observe(Player(11U, true, 20.0), start);
  Require(cold_start.size() == 2U &&
              cold_start[0].kind == MayhemSelectionEventKind::Armed &&
              cold_start[0].snapshot.phase == MayhemSelectionPhase::Armed &&
              cold_start[0].snapshot.pending_count == 2U &&
              cold_start[1].kind == MayhemSelectionEventKind::DeathTriggered &&
              cold_start[1].snapshot.phase ==
                  MayhemSelectionPhase::Triggered,
          "a dead cold start after the threshold must fail open for scanning");

  const auto detected = scheduler.OfferDetected(start);
  Require(detected.size() == 1U && detected[0].stage == 2U &&
              scheduler.Snapshot(start).completed_offers == 1U &&
              scheduler.HighFrequencyActive(start),
          "recognized names must not consume a queued pending click");

  const auto confirmed = scheduler.SelectionConfirmed(start);
  Require(confirmed.size() == 2U &&
              confirmed[0].kind ==
                  MayhemSelectionEventKind::SelectionConfirmed &&
              confirmed[0].stage == 2U &&
              confirmed[1].kind ==
                  MayhemSelectionEventKind::QueuedOfferTriggered &&
              confirmed[1].stage == 3U && scheduler.HighFrequencyActive(start),
          "glow confirm on stage two must keep queued stage three waiting");
  Require(scheduler.Observe(Player(11U, true, 15.0), start).empty(),
          "queued scanning must not fabricate an additional death event");

  const auto next_confirmed =
      scheduler.SelectionConfirmed(start + std::chrono::milliseconds{100});
  Require(next_confirmed.size() == 1U && next_confirmed[0].stage == 3U &&
              !scheduler.HighFrequencyActive(start +
                                             std::chrono::milliseconds{100}) &&
              scheduler.Snapshot(start).pending_count == 0U,
          "after draining the level-eleven queue, wait for the next threshold");
}

void TestLevelFifteenBacklogQueuesStagesThreeAndFour() {
  const auto start = std::chrono::steady_clock::time_point{};
  MayhemSelectionScheduler scheduler{2U};
  const auto trigger = scheduler.Observe(Player(15U, true, 30.0), start);
  Require(trigger.size() == 2U &&
              trigger[1].kind == MayhemSelectionEventKind::DeathTriggered &&
              trigger[1].stage == 3U &&
              trigger[1].snapshot.pending_count == 2U,
          "a level-fifteen cold-start death must trigger stage three");

  const auto third = scheduler.SelectionConfirmed(start);
  Require(third.size() == 2U && third[0].stage == 3U &&
              third[1].kind ==
                  MayhemSelectionEventKind::QueuedOfferTriggered &&
              third[1].stage == 4U && scheduler.HighFrequencyActive(start),
          "stage four must remain queued behind stage three until the next glow");

  const auto fourth =
      scheduler.SelectionConfirmed(start + std::chrono::milliseconds{100});
  Require(fourth.size() == 1U && fourth[0].stage == 4U &&
              scheduler.Snapshot(start).phase == MayhemSelectionPhase::Complete &&
              scheduler.Snapshot(start).pending_count == 0U &&
              !scheduler.Snapshot(start).next_stage.has_value(),
          "the fourth glow confirm must complete the schedule");
}

void TestPendingStaysHotAfterOldWindowWouldHaveExpired() {
  const auto start = std::chrono::steady_clock::time_point{};
  MayhemSelectionScheduler scheduler{3U};
  static_cast<void>(scheduler.Observe(Player(15U, false), start));
  static_cast<void>(scheduler.Observe(Player(15U, true, 1.0), start));
  static_cast<void>(
      scheduler.Observe(Player(15U, false), start + std::chrono::seconds{1}));
  Require(scheduler.HighFrequencyActive(start + std::chrono::seconds{7}),
          "pending click wait must remain active past the old eight-second window");
  const auto later =
      scheduler.Observe(Player(15U, false), start + std::chrono::seconds{14});
  Require(later.empty() &&
              scheduler.HighFrequencyActive(start + std::chrono::seconds{14}) &&
              scheduler.Snapshot(start + std::chrono::seconds{14}).pending_count ==
                  1U,
          "without a glow confirm, scanning must not expire at any later time");
}

void TestUnconfirmedWaitsThroughEightDeathTenAndEleven() {
  const auto start = std::chrono::steady_clock::time_point{};
  MayhemSelectionScheduler scheduler;
  static_cast<void>(scheduler.Observe(Player(1U, false), start));
  static_cast<void>(scheduler.OfferDetected(start));
  Require(scheduler.Snapshot(start).pending_count == 1U &&
              scheduler.HighFrequencyActive(start),
          "the opening offer must stay owed until a glow confirm");

  const auto death_eight =
      scheduler.Observe(Player(8U, true, 16.0), start + std::chrono::seconds{1});
  Require(death_eight.size() == 1U &&
              death_eight[0].kind == MayhemSelectionEventKind::DeathTriggered &&
              scheduler.Snapshot(start + std::chrono::seconds{1}).pending_count ==
                  2U &&
              scheduler.HighFrequencyActive(start + std::chrono::seconds{1}),
          "dying at eight without a glow confirm must keep waiting for a click");

  Require(scheduler.Observe(Player(10U, false), start + std::chrono::seconds{2})
                  .empty() &&
              scheduler.Snapshot(start + std::chrono::seconds{2}).pending_count ==
                  2U &&
              scheduler.HighFrequencyActive(start + std::chrono::seconds{2}),
          "alive at ten without a glow confirm must still wait for a left click");

  static_cast<void>(
      scheduler.Observe(Player(11U, false), start + std::chrono::seconds{3}));
  Require(scheduler.Snapshot(start + std::chrono::seconds{3}).pending_count ==
                  3U &&
              scheduler.HighFrequencyActive(start + std::chrono::seconds{3}) &&
              scheduler.Snapshot(start + std::chrono::seconds{3}).completed_offers ==
                  0U,
          "reaching eleven without a glow confirm must still wait for a click");
}

void TestMaximumWindowDoesNotStopPendingClickWait() {
  const auto start = std::chrono::steady_clock::time_point{};
  MayhemSelectionScheduler scheduler{3U};
  static_cast<void>(scheduler.Observe(Player(15U, false), start));
  static_cast<void>(scheduler.Observe(Player(15U, true, 60.0), start));
  static_cast<void>(scheduler.Observe(Player(15U, true, 0.0),
                                      start + std::chrono::seconds{59}));
  Require(scheduler.HighFrequencyActive(start + std::chrono::seconds{59}),
          "recognition must still be active immediately before the old hard limit");
  Require(scheduler.HighFrequencyActive(start + std::chrono::seconds{60}),
          "a pending click wait must survive the old maximum_window");
}

} // namespace

int main() {
  try {
    TestFirstOfferOpensAtGameStart();
    TestOcrDoesNotConsumePendingUntilClickGlow();
    TestClickGlowConsumesPendingAndLevelSixStaysIdle();
    TestLevelSevenKeepsWaitingForClickWhileAlive();
    TestFountainAfterRespawnStillWaitsIfUnconfirmed();
    TestQueuedEligibleRoundStaysHotOnSameDeath();
    TestLevelFifteenBacklogQueuesStagesThreeAndFour();
    TestPendingStaysHotAfterOldWindowWouldHaveExpired();
    TestUnconfirmedWaitsThroughEightDeathTenAndEleven();
    TestMaximumWindowDoesNotStopPendingClickWait();
    std::cout << "mayhem_selection_scheduler_test passed: "
                 "thresholds=start+7/11/15; click_glow_consumes; "
                 "pending_stays_hot\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "mayhem_selection_scheduler_test failed: " << error.what()
              << '\n';
    return 1;
  }
}
