#include "lol_assistant/state/phase1_game_state_reducer.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <stdexcept>
#include <type_traits>
#include <utility>

#include "lol_assistant/common/confidence.h"
#include "lol_assistant/common/geometry.h"

namespace lol_assistant::state {
namespace {

using common::AugmentOfferObservation;
using common::AugmentRecognition;
using common::CardSlotId;
using common::RecognitionState;

[[nodiscard]] bool HasNonWhitespace(const std::string_view value) noexcept {
  return std::any_of(value.begin(), value.end(), [](const char character) {
    return character != ' ' && character != '\t' && character != '\r' &&
           character != '\n';
  });
}

[[nodiscard]] std::optional<std::size_t> SlotIndex(
    const CardSlotId slot) noexcept {
  switch (slot) {
    case CardSlotId::Left:
      return 0U;
    case CardSlotId::Center:
      return 1U;
    case CardSlotId::Right:
      return 2U;
    case CardSlotId::Unknown:
      return std::nullopt;
  }
  return std::nullopt;
}

struct InspectedOffer final {
  std::array<const AugmentRecognition*, common::kAugmentCardCount>
      recognitions_by_slot{};
  std::array<std::string, common::kAugmentCardCount> signature{};
};

enum class InspectionResult : std::uint8_t {
  Complete = 0,
  Invalid,
  Incomplete,
};

[[nodiscard]] InspectionResult InspectOffer(
    const AugmentOfferObservation& observation,
    InspectedOffer& inspected) noexcept {
  if (!observation.IsValid()) {
    return InspectionResult::Invalid;
  }
  if (!observation.screen_detection.has_value() ||
      observation.screen_detection->state != common::DetectionState::Detected) {
    return InspectionResult::Incomplete;
  }

  for (const auto& recognition : observation.recognitions) {
    if (!recognition.has_value() ||
        recognition->state != RecognitionState::Recognized ||
        !recognition->augment_id.has_value()) {
      return InspectionResult::Incomplete;
    }
    const auto index = SlotIndex(recognition->slot);
    if (!index.has_value() || inspected.recognitions_by_slot[*index] != nullptr) {
      return InspectionResult::Incomplete;
    }
    inspected.recognitions_by_slot[*index] = &*recognition;
    inspected.signature[*index] = *recognition->augment_id;
  }

  for (const auto* recognition : inspected.recognitions_by_slot) {
    if (recognition == nullptr) {
      return InspectionResult::Incomplete;
    }
  }

  auto sorted_ids = inspected.signature;
  std::sort(sorted_ids.begin(), sorted_ids.end());
  if (std::adjacent_find(sorted_ids.begin(), sorted_ids.end()) !=
      sorted_ids.end()) {
    return InspectionResult::Incomplete;
  }
  return InspectionResult::Complete;
}

[[nodiscard]] bool IsStable(
    const AugmentOfferObservation& observation,
    const InspectedOffer& inspected,
    const float minimum_confidence) noexcept {
  if (observation.metadata.confidence.value < minimum_confidence ||
      observation.screen_detection->metadata.confidence.value <
          minimum_confidence) {
    return false;
  }
  return std::all_of(
      inspected.recognitions_by_slot.begin(),
      inspected.recognitions_by_slot.end(),
      [minimum_confidence](const AugmentRecognition* recognition) {
        return recognition->metadata.confidence.value >= minimum_confidence;
      });
}

}  // namespace

bool ReducerResult::IsSuccess() const noexcept {
  return code == ReducerErrorCode::Ok || code == ReducerErrorCode::NoChange;
}

bool Phase1GameStateSnapshot::IsValid() const noexcept {
  if ((champion.has_value() && !HasNonWhitespace(*champion)) ||
      (current_offer.has_value() && !current_offer->IsValid())) {
    return false;
  }
  if (!offer_round.has_value()) {
    return !current_offer.has_value() &&
           std::all_of(selected_augments.begin(), selected_augments.end(),
                       [](const auto& selected) {
                         return !selected.has_value();
                       });
  }
  if (*offer_round == 0U || *offer_round > kPhase1OfferRoundCount) {
    return false;
  }
  if (!current_offer.has_value() && *offer_round == 1U) {
    return false;
  }

  for (std::size_t index = 0U; index < selected_augments.size(); ++index) {
    const auto& selected = selected_augments[index];
    if (index >= *offer_round && selected.has_value()) {
      return false;
    }
    if (selected.has_value() &&
        (!selected->IsValid() ||
         selected->state != RecognitionState::Recognized)) {
      return false;
    }
  }
  return true;
}

Phase1GameStateReducer::Phase1GameStateReducer(
    const float minimum_stable_confidence)
    : minimum_stable_confidence_(minimum_stable_confidence) {
  if (!common::Confidence{minimum_stable_confidence}.IsValid()) {
    throw std::invalid_argument(
        "minimum_stable_confidence must be in the inclusive range [0, 1]");
  }
}

ReducerResult Phase1GameStateReducer::SetManualChampion(std::string champion) {
  std::scoped_lock lock{mutex_};
  if (!HasNonWhitespace(champion)) {
    return ResultLocked(ReducerErrorCode::InvalidChampion, false);
  }
  if (state_.champion == champion) {
    return ResultLocked(ReducerErrorCode::NoChange, false);
  }
  state_.champion = std::move(champion);
  ++state_.revision;
  return ResultLocked(ReducerErrorCode::Ok, true);
}

ReducerResult Phase1GameStateReducer::ApplyStableOffer(
    const AugmentOfferObservation& observation) {
  auto prepared = PrepareStableOffer(observation);
  if (!prepared.IsPrepared()) {
    return prepared.status;
  }
  return CommitPreparedOffer(std::move(*prepared.update));
}

PrepareOfferResult Phase1GameStateReducer::PrepareStableOffer(
    const AugmentOfferObservation& observation,
    const std::optional<std::string_view> selected_augment_id) const {
  std::scoped_lock lock{mutex_};

  InspectedOffer inspected;
  const auto inspection = InspectOffer(observation, inspected);
  if (inspection == InspectionResult::Invalid) {
    return {ResultLocked(ReducerErrorCode::InvalidObservation, false),
            std::nullopt};
  }
  if (inspection == InspectionResult::Incomplete) {
    return {ResultLocked(ReducerErrorCode::IncompleteOffer, false),
            std::nullopt};
  }
  if (!IsStable(observation, inspected, minimum_stable_confidence_)) {
    return {ResultLocked(ReducerErrorCode::LowConfidence, false),
            std::nullopt};
  }
  if (seen_offer_signatures_.contains(inspected.signature)) {
    return {ResultLocked(ReducerErrorCode::DuplicateOffer, false),
            std::nullopt};
  }

  Phase1GameStateSnapshot proposed = state_;
  proposed.offer_round = proposed.offer_round.value_or(1U);
  proposed.current_offer = observation;

  if (selected_augment_id.has_value()) {
    if (!HasNonWhitespace(*selected_augment_id)) {
      return {ResultLocked(ReducerErrorCode::SelectionUnconfirmed, false),
              std::nullopt};
    }
    const AugmentRecognition* matched = nullptr;
    for (const auto* recognition : inspected.recognitions_by_slot) {
      if (recognition->augment_id == selected_augment_id) {
        matched = recognition;
        break;
      }
    }
    if (matched == nullptr) {
      return {
          ResultLocked(ReducerErrorCode::SelectionNotInCurrentOffer, false),
          std::nullopt};
    }
    const auto selected_index =
        static_cast<std::size_t>(*proposed.offer_round - 1U);
    auto& selected = proposed.selected_augments[selected_index];
    if (selected.has_value() && selected->augment_id != matched->augment_id) {
      return {
          ResultLocked(ReducerErrorCode::SelectionAlreadyConfirmed, false),
          std::nullopt};
    }
    selected = *matched;
  }

  proposed.revision = state_.revision + 1U;
  PreparedOfferUpdate update{std::move(proposed), inspected.signature,
                             state_.revision};
  return {ResultLocked(ReducerErrorCode::Ok, false), std::move(update)};
}

ReducerResult Phase1GameStateReducer::CommitPreparedOffer(
    PreparedOfferUpdate prepared) {
  std::scoped_lock lock{mutex_};
  if (prepared.base_revision_ != state_.revision ||
      prepared.snapshot_.revision != prepared.base_revision_ + 1U ||
      !prepared.snapshot_.IsValid() ||
      !prepared.snapshot_.current_offer.has_value()) {
    return ResultLocked(ReducerErrorCode::StalePreparedOffer, false);
  }

  InspectedOffer inspected;
  if (InspectOffer(*prepared.snapshot_.current_offer, inspected) !=
          InspectionResult::Complete ||
      inspected.signature != prepared.signature_ ||
      !IsStable(*prepared.snapshot_.current_offer, inspected,
                minimum_stable_confidence_) ||
      seen_offer_signatures_.contains(prepared.signature_)) {
    return ResultLocked(ReducerErrorCode::StalePreparedOffer, false);
  }

  auto proposed_seen = seen_offer_signatures_;
  proposed_seen.insert(prepared.signature_);
  static_assert(
      std::is_nothrow_swappable_v<Phase1GameStateSnapshot>,
      "reducer commit requires a no-throw snapshot swap");
  using std::swap;
  swap(state_, prepared.snapshot_);
  seen_offer_signatures_.swap(proposed_seen);
  return ResultLocked(ReducerErrorCode::Ok, true);
}

ReducerResult Phase1GameStateReducer::RecordSelection(
    const std::optional<std::string_view> augment_id) {
  std::scoped_lock lock{mutex_};
  if (!state_.offer_round.has_value() || !state_.current_offer.has_value()) {
    return ResultLocked(ReducerErrorCode::NoCurrentOffer, false);
  }
  if (!augment_id.has_value() || !HasNonWhitespace(*augment_id)) {
    return ResultLocked(ReducerErrorCode::SelectionUnconfirmed, false);
  }

  const AugmentRecognition* matched = nullptr;
  for (const auto& recognition : state_.current_offer->recognitions) {
    if (recognition.has_value() && recognition->augment_id.has_value() &&
        *recognition->augment_id == *augment_id) {
      matched = &*recognition;
      break;
    }
  }
  if (matched == nullptr) {
    return ResultLocked(ReducerErrorCode::SelectionNotInCurrentOffer, false);
  }

  const auto selected_index =
      static_cast<std::size_t>(*state_.offer_round - 1U);
  auto& selected = state_.selected_augments[selected_index];
  if (selected.has_value()) {
    if (selected->augment_id == matched->augment_id) {
      return ResultLocked(ReducerErrorCode::NoChange, false);
    }
    return ResultLocked(ReducerErrorCode::SelectionAlreadyConfirmed, false);
  }

  selected = *matched;
  ++state_.revision;
  return ResultLocked(ReducerErrorCode::Ok, true);
}

ReducerResult Phase1GameStateReducer::AdvanceRound() {
  std::scoped_lock lock{mutex_};
  if (!state_.offer_round.has_value() || !state_.current_offer.has_value()) {
    return ResultLocked(ReducerErrorCode::NoCurrentOffer, false);
  }
  if (*state_.offer_round >= kPhase1OfferRoundCount) {
    return ResultLocked(ReducerErrorCode::OfferRoundLimitReached, false);
  }

  Phase1GameStateSnapshot proposed = state_;
  ++*proposed.offer_round;
  proposed.current_offer.reset();
  ++proposed.revision;
  static_assert(
      std::is_nothrow_swappable_v<Phase1GameStateSnapshot>,
      "reducer round advance requires a no-throw snapshot swap");
  using std::swap;
  swap(state_, proposed);
  return ResultLocked(ReducerErrorCode::Ok, true);
}

ReducerResult Phase1GameStateReducer::ScreenDismissed() {
  return AdvanceRound();
}

Phase1GameStateSnapshot Phase1GameStateReducer::Snapshot() const {
  std::scoped_lock lock{mutex_};
  return state_;
}

float Phase1GameStateReducer::MinimumStableConfidence() const noexcept {
  return minimum_stable_confidence_;
}

ReducerResult Phase1GameStateReducer::ResultLocked(
    const ReducerErrorCode code, const bool changed) const noexcept {
  return ReducerResult{code, changed, state_.revision};
}

}  // namespace lol_assistant::state
