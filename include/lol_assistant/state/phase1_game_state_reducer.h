#pragma once

#include <array>
#include <cstdint>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <utility>

#include "lol_assistant/common/augment_observation.h"

namespace lol_assistant::state {

inline constexpr std::size_t kPhase1OfferRoundCount = 4U;

enum class ReducerErrorCode : std::uint8_t {
  Ok = 0,
  NoChange,
  InvalidChampion,
  InvalidObservation,
  IncompleteOffer,
  LowConfidence,
  DuplicateOffer,
  OfferRoundLimitReached,
  NoCurrentOffer,
  SelectionUnconfirmed,
  SelectionNotInCurrentOffer,
  SelectionAlreadyConfirmed,
  StalePreparedOffer,
};

struct ReducerResult final {
  ReducerErrorCode code{ReducerErrorCode::Ok};
  bool state_changed{false};
  std::uint64_t revision{0U};

  [[nodiscard]] bool IsSuccess() const noexcept;
};

struct Phase1GameStateSnapshot final {
  std::optional<std::string> champion{};
  std::optional<std::uint32_t> offer_round{};
  std::array<std::optional<common::AugmentRecognition>,
             kPhase1OfferRoundCount>
      selected_augments{};
  std::optional<common::AugmentOfferObservation> current_offer{};
  std::uint64_t revision{0U};

  [[nodiscard]] bool IsValid() const noexcept;
};

using OfferSignature =
    std::array<std::string, common::kAugmentCardCount>;

// A complete reducer mutation assembled without changing live state. The
// caller may persist snapshot first and commit this value only after durable
// persistence succeeds.
class PreparedOfferUpdate final {
 public:
  PreparedOfferUpdate(const PreparedOfferUpdate&) = default;
  PreparedOfferUpdate& operator=(const PreparedOfferUpdate&) = default;
  PreparedOfferUpdate(PreparedOfferUpdate&&) noexcept = default;
  PreparedOfferUpdate& operator=(PreparedOfferUpdate&&) noexcept = default;

  [[nodiscard]] const Phase1GameStateSnapshot& Snapshot() const noexcept {
    return snapshot_;
  }

 private:
  friend class Phase1GameStateReducer;

  PreparedOfferUpdate(Phase1GameStateSnapshot snapshot,
                      OfferSignature signature,
                      std::uint64_t base_revision)
      : snapshot_(std::move(snapshot)),
        signature_(std::move(signature)),
        base_revision_(base_revision) {}

  Phase1GameStateSnapshot snapshot_{};
  OfferSignature signature_{};
  std::uint64_t base_revision_{0U};
};

struct PrepareOfferResult final {
  ReducerResult status{};
  std::optional<PreparedOfferUpdate> update{};

  [[nodiscard]] bool IsPrepared() const noexcept {
    return status.code == ReducerErrorCode::Ok && update.has_value();
  }
};

class Phase1GameStateReducer final {
 public:
  explicit Phase1GameStateReducer(float minimum_stable_confidence = 0.85F);

  Phase1GameStateReducer(const Phase1GameStateReducer&) = delete;
  Phase1GameStateReducer& operator=(const Phase1GameStateReducer&) = delete;

  [[nodiscard]] ReducerResult SetManualChampion(std::string champion);
  [[nodiscard]] ReducerResult ApplyStableOffer(
      const common::AugmentOfferObservation& observation);
  [[nodiscard]] PrepareOfferResult PrepareStableOffer(
      const common::AugmentOfferObservation& observation,
      std::optional<std::string_view> selected_augment_id = std::nullopt) const;
  [[nodiscard]] ReducerResult CommitPreparedOffer(
      PreparedOfferUpdate prepared);
  [[nodiscard]] ReducerResult RecordSelection(
      std::optional<std::string_view> augment_id);
  [[nodiscard]] ReducerResult AdvanceRound();
  [[nodiscard]] ReducerResult ScreenDismissed();

  [[nodiscard]] Phase1GameStateSnapshot Snapshot() const;
  [[nodiscard]] float MinimumStableConfidence() const noexcept;

 private:
  [[nodiscard]] ReducerResult ResultLocked(ReducerErrorCode code,
                                           bool changed) const noexcept;

  const float minimum_stable_confidence_;
  mutable std::mutex mutex_{};
  Phase1GameStateSnapshot state_{};
  std::set<OfferSignature> seen_offer_signatures_{};
};

}  // namespace lol_assistant::state
