#include "lol_assistant/vision/icon_matcher.h"

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstddef>
#include <map>
#include <stdexcept>
#include <utility>

#include "lol_assistant/vision/text_matcher.h"

namespace lol_assistant::vision {
namespace {

constexpr std::uint32_t kHashWidth = 9U;
constexpr std::uint32_t kHashHeight = 8U;

[[nodiscard]] std::uint32_t PixelLuma(
    const detector::OwningBgraCrop& crop, const std::uint32_t x,
    const std::uint32_t y) noexcept {
  const auto offset = static_cast<std::size_t>(y) * crop.stride +
                      static_cast<std::size_t>(x) * 4U;
  const std::uint32_t blue = crop.pixels[offset + 0U];
  const std::uint32_t green = crop.pixels[offset + 1U];
  const std::uint32_t red = crop.pixels[offset + 2U];
  return (29U * blue + 150U * green + 77U * red) >> 8U;
}

// Deterministic bilinear resize to the 9x8 dHash grid. Endpoint mapping keeps
// the full icon ROI in scope and uses integer arithmetic so imported hashes and
// runtime hashes can agree byte-for-byte across toolchains.
[[nodiscard]] std::uint8_t SampleResizedLuma(
    const detector::OwningBgraCrop& crop, const std::uint32_t target_x,
    const std::uint32_t target_y) noexcept {
  constexpr std::uint32_t x_denominator = kHashWidth - 1U;
  constexpr std::uint32_t y_denominator = kHashHeight - 1U;
  const std::uint64_t x_numerator =
      static_cast<std::uint64_t>(target_x) * (crop.width - 1U);
  const std::uint64_t y_numerator =
      static_cast<std::uint64_t>(target_y) * (crop.height - 1U);
  const auto x0 = static_cast<std::uint32_t>(x_numerator / x_denominator);
  const auto y0 = static_cast<std::uint32_t>(y_numerator / y_denominator);
  const auto x1 = std::min(x0 + 1U, crop.width - 1U);
  const auto y1 = std::min(y0 + 1U, crop.height - 1U);
  const auto x_remainder =
      static_cast<std::uint32_t>(x_numerator % x_denominator);
  const auto y_remainder =
      static_cast<std::uint32_t>(y_numerator % y_denominator);

  const std::uint64_t top =
      static_cast<std::uint64_t>(PixelLuma(crop, x0, y0)) *
          (x_denominator - x_remainder) +
      static_cast<std::uint64_t>(PixelLuma(crop, x1, y0)) * x_remainder;
  const std::uint64_t bottom =
      static_cast<std::uint64_t>(PixelLuma(crop, x0, y1)) *
          (x_denominator - x_remainder) +
      static_cast<std::uint64_t>(PixelLuma(crop, x1, y1)) * x_remainder;
  constexpr std::uint64_t denominator =
      static_cast<std::uint64_t>(x_denominator) * y_denominator;
  const std::uint64_t value =
      top * (y_denominator - y_remainder) + bottom * y_remainder;
  return static_cast<std::uint8_t>((value + denominator / 2U) / denominator);
}

[[nodiscard]] float ScoreFromDistance(const std::uint32_t distance) noexcept {
  return 1.0F - static_cast<float>(distance) / 64.0F;
}

[[nodiscard]] bool SupportsMode(
    const IconHashTemplate& item,
    const std::optional<std::string_view> mode) noexcept {
  if (!mode.has_value() || item.modes.empty()) {
    return true;
  }
  return std::find(item.modes.begin(), item.modes.end(), *mode) !=
         item.modes.end();
}

}  // namespace

PerceptualHashResult ComputeDifferenceHash(
    const detector::OwningBgraCrop& crop) noexcept {
  if (!crop.IsValid()) {
    return {std::nullopt, "invalid_bgra_crop"};
  }
  std::uint64_t hash = 0U;
  std::uint32_t bit = 0U;
  for (std::uint32_t y = 0U; y < kHashHeight; ++y) {
    for (std::uint32_t x = 0U; x + 1U < kHashWidth; ++x) {
      if (SampleResizedLuma(crop, x, y) <
          SampleResizedLuma(crop, x + 1U, y)) {
        hash |= std::uint64_t{1U} << bit;
      }
      ++bit;
    }
  }
  return {hash, "computed"};
}

PerceptualHashTemplateMatcher::PerceptualHashTemplateMatcher(
    std::vector<IconHashTemplate> templates,
    const std::uint32_t maximum_hamming_distance, const float minimum_margin)
    : templates_(std::move(templates)),
      maximum_hamming_distance_(maximum_hamming_distance),
      minimum_margin_(minimum_margin) {
  if (maximum_hamming_distance_ > 64U || !std::isfinite(minimum_margin_) ||
      minimum_margin_ < 0.0F || minimum_margin_ > 1.0F) {
    throw std::invalid_argument("invalid icon template matcher config");
  }
  for (const auto& item : templates_) {
    if (item.augment_id.empty()) {
      throw std::invalid_argument("icon template id must be non-empty");
    }
    if (std::any_of(item.modes.begin(), item.modes.end(),
                    [](const std::string& mode) { return mode.empty(); })) {
      throw std::invalid_argument("icon template mode must be non-empty");
    }
  }
}

IconMatchResult PerceptualHashTemplateMatcher::Match(
    const detector::OwningBgraCrop& crop,
    const std::optional<std::string_view> mode) const noexcept {
  if (templates_.empty()) {
    return {};
  }
  if (mode.has_value() && mode->empty()) {
    IconMatchResult result;
    result.state = IconMatchState::Unknown;
    result.reason = "invalid_mode";
    return result;
  }
  if (std::none_of(templates_.begin(), templates_.end(),
                   [mode](const IconHashTemplate& item) {
                     return SupportsMode(item, mode);
                   })) {
    IconMatchResult result;
    result.reason = "template_unavailable_for_mode";
    return result;
  }
  const auto hash = ComputeDifferenceHash(crop);
  if (!hash.ok()) {
    IconMatchResult result;
    result.state = IconMatchState::Unknown;
    result.reason = hash.reason;
    return result;
  }

  struct Score final {
    std::string augment_id{};
    std::uint32_t distance{64U};
  };

  std::map<std::string, std::uint32_t> best_distance_by_id;
  for (const auto& item : templates_) {
    if (!SupportsMode(item, mode)) {
      continue;
    }
    const auto distance = static_cast<std::uint32_t>(
        std::popcount(*hash.hash ^ item.difference_hash));
    const auto [iterator, inserted] =
        best_distance_by_id.try_emplace(item.augment_id, distance);
    if (!inserted) {
      iterator->second = std::min(iterator->second, distance);
    }
  }
  if (best_distance_by_id.empty()) {
    IconMatchResult result;
    result.reason = "template_unavailable_for_mode";
    return result;
  }

  std::vector<Score> scores;
  scores.reserve(best_distance_by_id.size());
  for (const auto& [augment_id, distance] : best_distance_by_id) {
    scores.push_back({augment_id, distance});
  }
  std::sort(scores.begin(), scores.end(),
            [](const Score& left, const Score& right) {
              if (left.distance != right.distance) {
                return left.distance < right.distance;
              }
              return left.augment_id < right.augment_id;
            });

  IconMatchResult result;
  result.state = IconMatchState::Unknown;
  const float top1 = ScoreFromDistance(scores[0].distance);
  result.confidence = top1;
  result.top1_score = top1;
  result.candidate_ids.push_back(scores[0].augment_id);
  float margin = top1;
  if (scores.size() > 1U) {
    const float top2 = ScoreFromDistance(scores[1].distance);
    result.top2_score = top2;
    result.candidate_ids.push_back(scores[1].augment_id);
    margin = top1 - top2;
  }
  result.margin = margin;

  if (scores[0].distance > maximum_hamming_distance_) {
    result.reason = "hash_distance_above_threshold";
    return result;
  }
  if (scores.size() > 1U && scores[0].distance == scores[1].distance) {
    result.reason = "hash_top1_ambiguous";
    return result;
  }
  if (scores.size() > 1U && margin < minimum_margin_) {
    result.reason = "hash_margin_below_threshold";
    return result;
  }
  result.state = IconMatchState::Matched;
  result.augment_id = scores[0].augment_id;
  result.reason = "hash_match";
  return result;
}

FusedAugmentResult FuseAugmentIdentity(const TextMatchResult& ocr_match,
                                       const IconMatchResult& icon_match) {
  const bool ocr_identified = ocr_match.matched() &&
                              ocr_match.id.has_value() &&
                              !ocr_match.id->empty();
  const bool icon_identified =
      icon_match.state == IconMatchState::Matched &&
      icon_match.augment_id.has_value() && !icon_match.augment_id->empty();

  if (icon_match.state == IconMatchState::Matched && !icon_identified) {
    return {FusedAugmentState::Unknown, std::nullopt,
            "invalid_icon_match_result"};
  }
  if (ocr_identified && icon_identified) {
    if (*ocr_match.id == *icon_match.augment_id) {
      return {FusedAugmentState::Confirmed, *ocr_match.id,
              "ocr_icon_agree"};
    }
    return {FusedAugmentState::Unknown, std::nullopt,
            "ocr_icon_conflict:ocr=" + *ocr_match.id +
                ",icon=" + *icon_match.augment_id};
  }
  if (ocr_identified) {
    const std::string suffix =
        icon_match.state == IconMatchState::Unavailable ? "unavailable"
                                                        : "unknown";
    return {FusedAugmentState::Tentative, *ocr_match.id,
            "ocr_only_icon_" + suffix};
  }
  if (icon_identified) {
    return {FusedAugmentState::Tentative, *icon_match.augment_id,
            "icon_only_ocr_unknown"};
  }
  return {FusedAugmentState::Unknown, std::nullopt,
          "both_unknown:ocr=" + ocr_match.reason +
              ",icon=" + icon_match.reason};
}

}  // namespace lol_assistant::vision
