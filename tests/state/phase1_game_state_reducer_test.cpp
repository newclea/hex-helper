#include "lol_assistant/state/phase1_game_state_reducer.h"

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <exception>
#include <functional>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "lol_assistant/common/contracts.h"

namespace {

using lol_assistant::common::AugmentOfferObservation;
using lol_assistant::common::AugmentRecognition;
using lol_assistant::common::AugmentScreenDetection;
using lol_assistant::common::CardSlot;
using lol_assistant::common::CardSlotId;
using lol_assistant::common::Confidence;
using lol_assistant::common::DetectionState;
using lol_assistant::common::NormalizedRoi;
using lol_assistant::common::ObservationMetadata;
using lol_assistant::common::RecognitionState;
using lol_assistant::state::Phase1GameStateReducer;
using lol_assistant::state::ReducerErrorCode;

void Require(const bool condition, const std::string_view message) {
  if (!condition) {
    throw std::runtime_error(std::string{message});
  }
}

[[nodiscard]] ObservationMetadata Metadata(const float confidence) {
  ObservationMetadata metadata;
  metadata.source = "state-unit-test";
  metadata.confidence = Confidence{confidence};
  metadata.observed_at = std::chrono::system_clock::now();
  return metadata;
}

[[nodiscard]] AugmentOfferObservation Offer(
    const std::string& prefix, const float confidence = 0.95F) {
  AugmentOfferObservation offer;
  offer.metadata = Metadata(confidence);

  AugmentScreenDetection detection;
  detection.state = DetectionState::Detected;
  detection.offer_roi = NormalizedRoi{0.1, 0.2, 0.8, 0.5};
  detection.card_slots = {
      CardSlot{CardSlotId::Left, NormalizedRoi{0.1, 0.2, 0.2, 0.5}},
      CardSlot{CardSlotId::Center, NormalizedRoi{0.4, 0.2, 0.2, 0.5}},
      CardSlot{CardSlotId::Right, NormalizedRoi{0.7, 0.2, 0.2, 0.5}},
  };
  detection.metadata = Metadata(confidence);
  offer.screen_detection = detection;

  constexpr std::array slots{CardSlotId::Left, CardSlotId::Center,
                             CardSlotId::Right};
  for (std::size_t index = 0U; index < slots.size(); ++index) {
    AugmentRecognition recognition;
    recognition.state = RecognitionState::Recognized;
    recognition.slot = slots[index];
    recognition.augment_id = prefix + "_" + std::to_string(index + 1U);
    recognition.display_name = "Augment " + std::to_string(index + 1U);
    recognition.metadata = Metadata(confidence);
    offer.recognitions[index] = std::move(recognition);
  }
  return offer;
}

void TestInitialStateAndManualChampion() {
  Phase1GameStateReducer reducer;
  const auto initial = reducer.Snapshot();
  Require(initial.IsValid(), "initial snapshot must be valid");
  Require(!initial.offer_round.has_value(), "initial round must be null");
  Require(!initial.current_offer.has_value(), "initial offer must be null");

  const auto invalid = reducer.SetManualChampion(" \t");
  Require(invalid.code == ReducerErrorCode::InvalidChampion,
          "blank champion must be rejected");
  Require(!invalid.state_changed, "invalid champion must not mutate state");

  const auto applied = reducer.SetManualChampion("Ahri");
  Require(applied.IsSuccess() && applied.state_changed,
          "manual champion must be applied");
  const auto duplicate = reducer.SetManualChampion("Ahri");
  Require(duplicate.code == ReducerErrorCode::NoChange,
          "same champion must be idempotent");
  Require(reducer.Snapshot().champion == "Ahri", "champion snapshot mismatch");
}

void TestOfferCorrectionDoesNotAdvanceRound() {
  Phase1GameStateReducer reducer;
  const auto first = Offer("round1_a");
  const auto accepted = reducer.ApplyStableOffer(first);
  Require(accepted.code == ReducerErrorCode::Ok && accepted.state_changed,
          "first complete offer must establish round one");
  Require(reducer.Snapshot().offer_round == 1U, "first round must be one");

  const auto correction = reducer.ApplyStableOffer(Offer("round1_b"));
  const auto corrected = reducer.Snapshot();
  Require(correction.code == ReducerErrorCode::Ok &&
              corrected.offer_round == 1U,
          "a different same-screen offer must correct the current round");
  Require(corrected.current_offer->recognitions[0]->augment_id ==
              "round1_b_1",
          "same-round correction must replace the current offer");

  const auto duplicate = reducer.ApplyStableOffer(first);
  Require(duplicate.code == ReducerErrorCode::DuplicateOffer,
          "a committed offer signature must remain deduplicated");
  Require(duplicate.revision == correction.revision,
          "duplicate offer must not change revision");
}

void TestExplicitDismissalAdvancesAndCapsRounds() {
  Phase1GameStateReducer reducer;
  Require(reducer.ScreenDismissed().code == ReducerErrorCode::NoCurrentOffer,
          "dismissal before an accepted offer must not invent a round");

  for (std::uint32_t round = 1U; round <= 4U; ++round) {
    const auto prefix = "round" + std::to_string(round);
    const auto offer = reducer.ApplyStableOffer(Offer(prefix));
    Require(offer.code == ReducerErrorCode::Ok,
            "each explicit round must accept one stable offer");
    const auto accepted = reducer.Snapshot();
    Require(accepted.offer_round == round && accepted.current_offer.has_value(),
            "offer must stay in the explicitly active round");
    Require(!accepted.selected_augments[round - 1U].has_value(),
            "an empty choice must be legal for every round");

    const auto dismissed = reducer.ScreenDismissed();
    if (round < 4U) {
      Require(dismissed.code == ReducerErrorCode::Ok &&
                  dismissed.state_changed,
              "dismissal must explicitly advance before round four");
      const auto between_rounds = reducer.Snapshot();
      Require(between_rounds.offer_round == round + 1U &&
                  !between_rounds.current_offer.has_value() &&
                  between_rounds.IsValid(),
              "between-round snapshot must be valid with no current offer");
    } else {
      Require(dismissed.code == ReducerErrorCode::OfferRoundLimitReached &&
                  !dismissed.state_changed,
              "round four dismissal must not create round five");
      Require(reducer.Snapshot().offer_round == 4U,
              "phase-one round must remain capped at four");
    }
  }
}

void TestPrepareCommitAndDiscardAreAtomic() {
  Phase1GameStateReducer reducer;
  Require(reducer.SetManualChampion("Lux").IsSuccess(),
          "champion setup failed");
  const auto before = reducer.Snapshot();

  auto discarded = reducer.PrepareStableOffer(Offer("retry"), "retry_2");
  Require(discarded.IsPrepared(), "valid offer must prepare");
  Require(discarded.update->Snapshot().offer_round == 1U &&
              discarded.update->Snapshot()
                      .selected_augments[0]
                      ->augment_id ==
                  "retry_2",
          "prepared snapshot must contain the full offer and selection");
  const auto after_prepare = reducer.Snapshot();
  Require(after_prepare.revision == before.revision &&
              !after_prepare.offer_round.has_value() &&
              !after_prepare.current_offer.has_value(),
          "prepare/discard must leave live state and revision untouched");

  discarded.update.reset();
  auto retry = reducer.PrepareStableOffer(Offer("retry"), "retry_2");
  Require(retry.IsPrepared(),
          "discarded persistence attempt must not consume its signature");
  const auto committed =
      reducer.CommitPreparedOffer(std::move(*retry.update));
  const auto after_commit = reducer.Snapshot();
  Require(committed.code == ReducerErrorCode::Ok && committed.state_changed,
          "successful persistence must permit one reducer commit");
  Require(after_commit.revision == before.revision + 1U &&
              after_commit.offer_round == 1U &&
              after_commit.selected_augments[0]->augment_id == "retry_2",
          "offer and selection must commit as one revision");

  auto stale = reducer.PrepareStableOffer(Offer("stale"));
  Require(stale.IsPrepared(), "stale transition setup failed");
  Require(reducer.SetManualChampion("Ahri").IsSuccess(),
          "intervening mutation setup failed");
  const auto before_stale_commit = reducer.Snapshot();
  const auto stale_commit =
      reducer.CommitPreparedOffer(std::move(*stale.update));
  const auto after_stale_commit = reducer.Snapshot();
  Require(stale_commit.code == ReducerErrorCode::StalePreparedOffer &&
              after_stale_commit.revision == before_stale_commit.revision &&
              after_stale_commit.current_offer->recognitions[0]->augment_id ==
                  "retry_1",
          "stale commit must not alter state or seen signatures");

  auto recovered = reducer.PrepareStableOffer(Offer("stale"));
  Require(recovered.IsPrepared(),
          "stale rejected transition signature must remain retryable");
}

void TestIncompleteAndLowConfidenceDoNotAdvance() {
  Phase1GameStateReducer reducer;
  auto incomplete = Offer("incomplete");
  incomplete.recognitions[2] = std::nullopt;
  Require(reducer.ApplyStableOffer(incomplete).code ==
              ReducerErrorCode::IncompleteOffer,
          "incomplete three-card offer must be rejected");
  Require(!reducer.Snapshot().offer_round.has_value(),
          "incomplete offer must not advance round");

  Require(reducer.ApplyStableOffer(Offer("low", 0.60F)).code ==
              ReducerErrorCode::LowConfidence,
          "low-confidence offer must be rejected");
  Require(!reducer.Snapshot().offer_round.has_value(),
          "low confidence must not advance round");

  auto invalid = Offer("invalid");
  invalid.metadata.confidence = Confidence{1.5F};
  Require(reducer.ApplyStableOffer(invalid).code ==
              ReducerErrorCode::InvalidObservation,
          "contract-invalid observation must have a distinct error");
}

void TestSelectionMustComeFromCurrentOffer() {
  Phase1GameStateReducer reducer;
  Require(reducer.RecordSelection("anything").code ==
              ReducerErrorCode::NoCurrentOffer,
          "selection without an offer must fail");
  Require(reducer.ApplyStableOffer(Offer("round1")).IsSuccess(),
          "round one offer setup failed");
  Require(reducer.RecordSelection(std::nullopt).code ==
              ReducerErrorCode::SelectionUnconfirmed,
          "unconfirmed selection must remain explicit and nullable");
  Require(reducer.RecordSelection("not_in_offer").code ==
              ReducerErrorCode::SelectionNotInCurrentOffer,
          "out-of-offer selection must be rejected");
  Require(reducer.RecordSelection("round1_2").IsSuccess(),
          "current-offer selection must be accepted");
  Require(reducer.RecordSelection("round1_2").code ==
              ReducerErrorCode::NoChange,
          "same confirmed selection must be idempotent");
  Require(reducer.RecordSelection("round1_3").code ==
              ReducerErrorCode::SelectionAlreadyConfirmed,
          "confirmed selection must not be overwritten");

  Require(reducer.AdvanceRound().IsSuccess(),
          "explicit advance to round two failed");
  Require(reducer.ApplyStableOffer(Offer("round2")).IsSuccess(),
          "round two offer setup failed");
  Require(reducer.RecordSelection("round1_1").code ==
              ReducerErrorCode::SelectionNotInCurrentOffer,
          "a prior-round augment must not be selectable");
  const auto snapshot = reducer.Snapshot();
  Require(snapshot.selected_augments[0].has_value() &&
              !snapshot.selected_augments[1].has_value() && snapshot.IsValid(),
          "selection history/current null choice invariant failed");
}

void TestConcurrentSnapshotsRemainValid() {
  Phase1GameStateReducer reducer;
  std::atomic<bool> stop{false};
  std::atomic<int> invalid_snapshots{0};
  std::thread reader([&] {
    while (!stop.load(std::memory_order_acquire)) {
      if (!reducer.Snapshot().IsValid()) {
        invalid_snapshots.fetch_add(1, std::memory_order_relaxed);
      }
    }
  });

  bool mutations_succeeded = reducer.SetManualChampion("Lux").IsSuccess();
  for (std::uint32_t round = 1U; round <= 4U; ++round) {
    const auto prefix = "thread" + std::to_string(round);
    auto prepared =
        reducer.PrepareStableOffer(Offer(prefix), prefix + "_1");
    mutations_succeeded = mutations_succeeded && prepared.IsPrepared();
    if (prepared.IsPrepared()) {
      mutations_succeeded =
          mutations_succeeded &&
          reducer.CommitPreparedOffer(std::move(*prepared.update)).IsSuccess();
    }
    if (round < 4U) {
      mutations_succeeded =
          mutations_succeeded && reducer.AdvanceRound().IsSuccess();
    }
  }
  stop.store(true, std::memory_order_release);
  reader.join();
  Require(mutations_succeeded, "concurrent test mutations must succeed");
  Require(invalid_snapshots.load(std::memory_order_relaxed) == 0,
          "concurrent snapshot observed an invalid state");
  Require(reducer.Snapshot().revision == 8U,
          "combined commits and explicit advances must own one revision each");
}

}  // namespace

int main() {
  const std::vector<std::pair<std::string_view, std::function<void()>>> tests{
      {"initial state and champion", TestInitialStateAndManualChampion},
      {"same-round offer correction", TestOfferCorrectionDoesNotAdvanceRound},
      {"explicit dismissal and round cap",
       TestExplicitDismissalAdvancesAndCapsRounds},
      {"prepare commit discard atomicity", TestPrepareCommitAndDiscardAreAtomic},
      {"incomplete and confidence gates",
       TestIncompleteAndLowConfidenceDoNotAdvance},
      {"selection invariants", TestSelectionMustComeFromCurrentOffer},
      {"thread-safe snapshots", TestConcurrentSnapshotsRemainValid},
  };

  std::size_t passed = 0U;
  for (const auto& [name, test] : tests) {
    try {
      test();
      ++passed;
      std::cout << "[PASS] " << name << '\n';
    } catch (const std::exception& error) {
      std::cerr << "[FAIL] " << name << ": " << error.what() << '\n';
    }
  }
  std::cout << "State tests: " << passed << '/' << tests.size() << " passed.\n";
  return passed == tests.size() ? 0 : 1;
}
