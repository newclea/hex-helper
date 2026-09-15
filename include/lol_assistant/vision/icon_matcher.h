#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "lol_assistant/detector/roi.h"

namespace lol_assistant::vision {

struct PerceptualHashResult final {
  std::optional<std::uint64_t> hash{};
  std::string reason{};

  [[nodiscard]] bool ok() const noexcept { return hash.has_value(); }
};

[[nodiscard]] PerceptualHashResult ComputeDifferenceHash(
    const detector::OwningBgraCrop& crop) noexcept;

struct IconHashTemplate final {
  std::string augment_id{};
  std::uint64_t difference_hash{0U};
  // Empty keeps backward-compatible "all modes" behavior. Imported
  // templates should list their explicit KIWI/KIWI_JADE membership.
  std::vector<std::string> modes{};
};

enum class IconMatchState : std::uint8_t {
  Unavailable = 0,
  Unknown = 1,
  Matched = 2,
};

struct IconMatchResult final {
  IconMatchState state{IconMatchState::Unavailable};
  std::optional<std::string> augment_id{};
  // Compatibility alias for top1_score.
  std::optional<float> confidence{};
  std::optional<float> top1_score{};
  std::optional<float> top2_score{};
  std::optional<float> margin{};
  // At most the top two distinct augment IDs, in deterministic rank order.
  std::vector<std::string> candidate_ids{};
  std::string reason{"template_unavailable"};

  [[nodiscard]] bool available() const noexcept {
    return state != IconMatchState::Unavailable;
  }
};

class PerceptualHashTemplateMatcher final {
 public:
  explicit PerceptualHashTemplateMatcher(
      std::vector<IconHashTemplate> templates = {},
      std::uint32_t maximum_hamming_distance = 10U,
      float minimum_margin = 0.08F);

  [[nodiscard]] IconMatchResult Match(
      const detector::OwningBgraCrop& crop,
      std::optional<std::string_view> mode = std::nullopt) const noexcept;

 private:
  std::vector<IconHashTemplate> templates_{};
  std::uint32_t maximum_hamming_distance_{10U};
  float minimum_margin_{0.08F};
};

struct TextMatchResult;

enum class FusedAugmentState : std::uint8_t {
  Unknown = 0,
  Tentative = 1,
  Confirmed = 2,
};

struct FusedAugmentResult final {
  FusedAugmentState state{FusedAugmentState::Unknown};
  std::optional<std::string> augment_id{};
  std::string reason{"both_unknown"};

  [[nodiscard]] bool confirmed() const noexcept {
    return state == FusedAugmentState::Confirmed && augment_id.has_value();
  }
};

// Conservative evidence policy:
// - OCR and icon agreeing on the same ID => Confirmed.
// - Exactly one source identifying an ID => Tentative.
// - Conflicting IDs => Unknown, with no selected ID.
[[nodiscard]] FusedAugmentResult FuseAugmentIdentity(
    const TextMatchResult& ocr_match, const IconMatchResult& icon_match);

}  // namespace lol_assistant::vision
