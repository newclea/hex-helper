#include "lol_assistant/vision/recognition_pipeline.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace lol_assistant::vision {
namespace {

constexpr std::size_t kMaximumAggregateOcrBytes =
    kMaximumOcrLineCandidates * kMaximumOcrLineCandidateBytes;
constexpr float kExactNoConfidenceHeuristic = 0.90F;
constexpr float kNormalizedNoConfidenceHeuristic = 0.86F;
constexpr float kFuzzyNoConfidenceCeiling = 0.82F;

void AppendUniqueBounded(std::vector<std::string> &inputs,
                         const std::string &input,
                         const std::size_t maximum_bytes) {
  if (input.empty() || input.size() > maximum_bytes ||
      std::find(inputs.begin(), inputs.end(), input) != inputs.end()) {
    return;
  }
  inputs.push_back(input);
}

[[nodiscard]] std::vector<std::string>
BuildMatchInputs(const OcrTextResult &ocr_result) {
  std::vector<std::string> inputs;
  inputs.reserve(1U + 2U * kMaximumOcrLineCandidates);
  AppendUniqueBounded(inputs, ocr_result.raw_text, kMaximumAggregateOcrBytes);

  const std::size_t line_count =
      std::min(ocr_result.line_candidates.size(), kMaximumOcrLineCandidates);
  for (std::size_t index = 0U; index < line_count; ++index) {
    AppendUniqueBounded(inputs, ocr_result.line_candidates[index],
                        kMaximumOcrLineCandidateBytes);
  }
  for (std::size_t index = 0U; index + 1U < line_count; ++index) {
    const auto &first = ocr_result.line_candidates[index];
    const auto &second = ocr_result.line_candidates[index + 1U];
    if (first.empty() || second.empty() ||
        first.size() > kMaximumOcrLineCandidateBytes ||
        second.size() > kMaximumOcrLineCandidateBytes) {
      continue;
    }
    std::string adjacent;
    adjacent.reserve(first.size() + 1U + second.size());
    adjacent.append(first);
    adjacent.push_back(' ');
    adjacent.append(second);
    AppendUniqueBounded(inputs, adjacent,
                        2U * kMaximumOcrLineCandidateBytes + 1U);
  }
  return inputs;
}

[[nodiscard]] int KindRank(const TextMatchKind kind) noexcept {
  switch (kind) {
  case TextMatchKind::Exact:
    return 3;
  case TextMatchKind::Normalized:
    return 2;
  case TextMatchKind::Fuzzy:
    return 1;
  case TextMatchKind::Unknown:
    return 0;
  }
  return 0;
}

[[nodiscard]] bool IsBetterMatch(const TextMatchResult &candidate,
                                 const TextMatchResult &current) noexcept {
  const int candidate_rank = KindRank(candidate.kind);
  const int current_rank = KindRank(current.kind);
  if (candidate_rank != current_rank) {
    return candidate_rank > current_rank;
  }
  if (candidate.top1_score != current.top1_score) {
    return candidate.top1_score > current.top1_score;
  }
  return candidate.margin > current.margin;
}

struct CandidateMatchSummary final {
  TextMatchResult best{};
  bool has_match{false};
  bool conflicting_ids{false};
};

[[nodiscard]] CandidateMatchSummary
MatchOcrCandidates(const OcrTextResult &ocr_result,
                   const BoundedTextMatcher &matcher,
                   const std::vector<TitleCandidate> &candidates) {
  CandidateMatchSummary summary;
  bool has_diagnostic = false;
  for (const auto &input : BuildMatchInputs(ocr_result)) {
    auto result = matcher.Match(input, candidates);
    if (!result.matched()) {
      if (!summary.has_match &&
          (!has_diagnostic || result.top1_score > summary.best.top1_score)) {
        summary.best = std::move(result);
        has_diagnostic = true;
      }
      continue;
    }
    if (summary.has_match && result.id != summary.best.id) {
      summary.conflicting_ids = true;
      if (IsBetterMatch(result, summary.best)) {
        summary.best = std::move(result);
      }
      continue;
    }
    if (!summary.has_match || IsBetterMatch(result, summary.best)) {
      summary.best = std::move(result);
    }
    summary.has_match = true;
  }
  if (!summary.has_match && !has_diagnostic) {
    summary.best.reason = "no_bounded_ocr_text_candidate";
  }
  return summary;
}

[[nodiscard]] float
NoConfidenceHeuristic(const TextMatchResult &match) noexcept {
  switch (match.kind) {
  case TextMatchKind::Exact:
    return kExactNoConfidenceHeuristic;
  case TextMatchKind::Normalized:
    return kNormalizedNoConfidenceHeuristic;
  case TextMatchKind::Fuzzy:
    return std::min(match.top1_score, kFuzzyNoConfidenceCeiling);
  case TextMatchKind::Unknown:
    return 0.0F;
  }
  return 0.0F;
}

void ApplyMatchToCard(CardRecognitionOutput &card, const OcrTextResult &ocr_result,
                      const CandidateMatchSummary &match_summary) {
  const auto &match = match_summary.best;
  card.raw_text = ocr_result.raw_text;
  card.backend = ocr_result.backend;
  card.ocr_confidence = ocr_result.ocr_confidence;
  card.reason = ocr_result.reason;
  card.match_kind = match.kind;
  card.normalized_text = match.normalized_text;
  card.match_confidence = match.top1_score;
  card.match_top2_score = match.top2_score;
  card.match_margin = match.margin;
  if (ocr_result.state == OcrResultState::BackendUnavailable) {
    card.state = CardRecognitionState::BackendUnavailable;
    return;
  }
  if (!ocr_result.ok()) {
    card.state = CardRecognitionState::OcrFailed;
    return;
  }
  if (match_summary.conflicting_ids) {
    card.state = CardRecognitionState::Unknown;
    card.reason = "conflicting_ocr_candidate_ids";
    return;
  }
  if (!match_summary.has_match) {
    card.state = CardRecognitionState::Unknown;
    card.reason = match.reason;
    return;
  }
  card.state = CardRecognitionState::Recognized;
  card.augment_id = match.id;
  card.display_name = match.title;
  card.reason = match.reason;
  if (card.ocr_confidence.has_value() && std::isfinite(*card.ocr_confidence)) {
    card.final_confidence = std::clamp(
        0.5F * *card.ocr_confidence + 0.5F * card.match_confidence, 0.0F, 1.0F);
  } else {
    card.final_confidence = NoConfidenceHeuristic(match);
  }
}

[[nodiscard]] CardRecognitionOutput RecognizeTitleCrop(
    const IOcrTitleRecognizer &ocr, const BoundedTextMatcher &matcher,
    const std::vector<TitleCandidate> &candidates, const common::Frame &frame,
    const detector::PixelRoi &roi) {
  CardRecognitionOutput card;
  const auto crop = detector::CropBgraOwning(frame, roi);
  if (!crop.ok()) {
    card.state = CardRecognitionState::OcrFailed;
    card.reason = crop.reason;
    return card;
  }
  const auto ocr_result = ocr.Recognize(*crop.value);
  ApplyMatchToCard(card, ocr_result,
                   MatchOcrCandidates(ocr_result, matcher, candidates));
  return card;
}

[[nodiscard]] bool IsHudCategoryTag(const std::string_view text) noexcept {
  return text == "伤害" || text == "复原力" || text == "复苏力" ||
         text == "机动" || text == "全能" || text == "成长" ||
         text == "经济" || text == "暴击几率" || text == "星界力" ||
         text == "圣毅力" || text == "任务" || text == "坦度" ||
         text == "爆发力";
}

[[nodiscard]] bool TitleLooksEmpty(const CardRecognitionOutput &card) noexcept {
  if (card.raw_text.empty() || card.normalized_text.empty()) {
    return true;
  }
  if (IsHudCategoryTag(card.normalized_text)) {
    return true;
  }
  // UTF-8 byte length used to treat "伤害" (6 bytes) as enough text and skip
  // the fallback band. Count CJK-ish characters instead.
  std::size_t units = 0U;
  for (const unsigned char value :
       std::string_view{card.normalized_text}) {
    if ((value & 0xC0U) != 0x80U) {
      ++units;
    }
  }
  return units < 3U;
}

[[nodiscard]] bool NeedsAlternateTitleBand(
    const CardRecognitionOutput &card) noexcept {
  return card.state == CardRecognitionState::Unknown && TitleLooksEmpty(card);
}

[[nodiscard]] bool IsGoldTitlePixel(const std::uint8_t *bgra) noexcept {
  const std::uint32_t blue = bgra[0];
  const std::uint32_t green = bgra[1];
  const std::uint32_t red = bgra[2];
  return red >= 168U && green >= 120U && blue <= 140U && red >= blue + 36U &&
         green >= blue + 16U;
}

[[nodiscard]] bool IsBrightTitlePixel(const std::uint8_t *bgra) noexcept {
  const std::uint32_t blue = bgra[0];
  const std::uint32_t green = bgra[1];
  const std::uint32_t red = bgra[2];
  if (red >= 196U && green >= 188U && blue >= 168U &&
      red + green + blue >= 580U) {
    return true;
  }
  return IsGoldTitlePixel(bgra);
}

[[nodiscard]] std::optional<detector::PixelRoi>
FindGoldTitleBand(const common::Frame &frame,
                  const detector::PixelRoi &card) noexcept {
  if (!frame.IsValid() || !card.IsInside(frame.width, frame.height) ||
      card.width < 24U || card.height < 24U) {
    return std::nullopt;
  }
  const auto inset = std::max<std::uint32_t>(2U, card.width / 20U);
  const auto x0 = card.x + inset;
  const auto x1 = card.x + card.width - inset;
  const auto y0 = card.y;
  const auto y1 = card.y + std::max<std::uint32_t>(12U, card.height * 42U / 100U);
  if (x1 <= x0 + 8U || y1 <= y0 + 6U) {
    return std::nullopt;
  }
  const auto rows = y1 - y0;
  const auto step = std::max<std::uint32_t>(1U, (x1 - x0) / 64U);
  std::uint32_t samples = 0U;
  for (auto x = x0; x < x1; x += step) {
    ++samples;
  }
  if (samples < 8U) {
    return std::nullopt;
  }
  std::vector<std::uint32_t> gold(rows, 0U);
  for (std::uint32_t y = y0; y < y1; ++y) {
    const auto row = static_cast<std::size_t>(y) * frame.stride;
    std::uint32_t count = 0U;
    for (auto x = x0; x < x1; x += step) {
      const auto *pixel =
          frame.buffer.data() + row +
          static_cast<std::size_t>(x) * common::Frame::kBytesPerPixel;
      if (IsBrightTitlePixel(pixel)) {
        ++count;
      }
    }
    gold[y - y0] = count;
  }
  const auto minimum = std::max<std::uint32_t>(2U, samples / 12U);
  std::optional<std::uint32_t> first;
  std::optional<std::uint32_t> last;
  std::uint32_t gap = 0U;
  for (std::uint32_t index = 0U; index < rows; ++index) {
    if (gold[index] >= minimum) {
      if (!first.has_value()) {
        first = index;
      }
      last = index;
      gap = 0U;
    } else if (first.has_value() && ++gap > 6U) {
      break;
    }
  }
  if (!first.has_value() || !last.has_value() || *last < *first) {
    return std::nullopt;
  }
  const auto band = *last - *first + 1U;
  if (band < 6U) {
    return std::nullopt;
  }
  const auto pad = std::max<std::uint32_t>(2U, band / 5U);
  auto top = y0 + *first;
  top = top > card.y + pad ? top - pad : card.y;
  const auto bottom = std::min(card.y + card.height, y0 + *last + 1U + pad);
  if (bottom <= top + 4U) {
    return std::nullopt;
  }
  detector::PixelRoi roi{x0, top, x1 - x0, bottom - top};
  roi.primary_ocr_rect = roi.Bounds();
  if (!roi.IsInside(frame.width, frame.height)) {
    return std::nullopt;
  }
  return roi;
}

[[nodiscard]] std::optional<detector::PixelRoi>
TitleBandWithinCard(const detector::PixelRoi &card, const double y_ratio,
                    const double height_ratio, const std::uint32_t frame_width,
                    const std::uint32_t frame_height) noexcept {
  if (card.width < 8U || card.height < 8U || !std::isfinite(y_ratio) ||
      !std::isfinite(height_ratio) || y_ratio < 0.0 || height_ratio <= 0.0 ||
      y_ratio + height_ratio > 1.0) {
    return std::nullopt;
  }
  const auto left =
      card.x + static_cast<std::uint32_t>(
                   std::floor(static_cast<double>(card.width) * 0.05));
  const auto top =
      card.y + static_cast<std::uint32_t>(
                   std::floor(static_cast<double>(card.height) * y_ratio));
  const auto right =
      card.x + static_cast<std::uint32_t>(
                   std::ceil(static_cast<double>(card.width) * 0.95));
  const auto bottom = card.y + static_cast<std::uint32_t>(std::ceil(
                                   static_cast<double>(card.height) *
                                   (y_ratio + height_ratio)));
  if (right <= left || bottom <= top || right > frame_width ||
      bottom > frame_height) {
    return std::nullopt;
  }
  detector::PixelRoi roi{left, top, right - left, bottom - top};
  roi.primary_ocr_rect = roi.Bounds();
  if (!roi.IsInside(frame_width, frame_height)) {
    return std::nullopt;
  }
  return roi;
}

constexpr std::uint32_t kScreenOcrMinWidth = 1280U;
constexpr std::uint32_t kScreenOcrMinHeight = 720U;
constexpr std::uint32_t kScreenOcrMaxSide = 1920U;

[[nodiscard]] detector::OwningBgraCrop DownscaleCrop(
    detector::OwningBgraCrop source, const std::uint32_t max_side) {
  if (source.width == 0U || source.height == 0U) {
    return source;
  }
  const auto longest = std::max(source.width, source.height);
  if (longest <= max_side) {
    return source;
  }
  const std::uint32_t factor = (longest + max_side - 1U) / max_side;
  detector::OwningBgraCrop scaled;
  scaled.width = std::max(1U, source.width / factor);
  scaled.height = std::max(1U, source.height / factor);
  scaled.stride = scaled.width * common::Frame::kBytesPerPixel;
  scaled.pixels.resize(static_cast<std::size_t>(scaled.stride) * scaled.height);
  for (std::uint32_t y = 0U; y < scaled.height; ++y) {
    for (std::uint32_t x = 0U; x < scaled.width; ++x) {
      const auto src_x = std::min(source.width - 1U, x * factor);
      const auto src_y = std::min(source.height - 1U, y * factor);
      const auto src = static_cast<std::size_t>(src_y) * source.stride +
                       static_cast<std::size_t>(src_x) *
                           common::Frame::kBytesPerPixel;
      const auto dst = static_cast<std::size_t>(y) * scaled.stride +
                       static_cast<std::size_t>(x) *
                           common::Frame::kBytesPerPixel;
      scaled.pixels[dst] = source.pixels[src];
      scaled.pixels[dst + 1U] = source.pixels[src + 1U];
      scaled.pixels[dst + 2U] = source.pixels[src + 2U];
      scaled.pixels[dst + 3U] = source.pixels[src + 3U];
    }
  }
  return scaled;
}

struct ScreenHit final {
  TextMatchResult match{};
  std::string raw{};
  float x{0.0F};
  float y{0.0F};
  std::size_t text_units{0U};
};

[[nodiscard]] std::size_t Utf8Units(const std::string_view text) noexcept {
  std::size_t units = 0U;
  for (const unsigned char value : text) {
    if ((value & 0xC0U) != 0x80U) {
      ++units;
    }
  }
  return units;
}

[[nodiscard]] bool BetterScreenHit(const ScreenHit &candidate,
                                   const ScreenHit &current) noexcept {
  if (IsBetterMatch(candidate.match, current.match)) {
    return true;
  }
  if (KindRank(candidate.match.kind) != KindRank(current.match.kind) ||
      candidate.match.top1_score != current.match.top1_score) {
    return false;
  }
  if (candidate.text_units != current.text_units) {
    return candidate.text_units < current.text_units;
  }
  return candidate.y < current.y;
}

[[nodiscard]] std::vector<OcrLineSpan> ScreenSpans(
    const OcrTextResult &ocr) {
  if (!ocr.line_spans.empty()) {
    return ocr.line_spans;
  }
  std::vector<OcrLineSpan> spans;
  for (const auto &line : ocr.line_candidates) {
    if (!line.empty()) {
      spans.push_back({line, 0.0F, 0.0F, 1.0F, 1.0F});
    }
  }
  if (spans.empty() && !ocr.raw_text.empty()) {
    spans.push_back({ocr.raw_text, 0.0F, 0.0F, 1.0F, 1.0F});
  }
  return spans;
}

[[nodiscard]] OfferRecognitionOutput RecognizeScreenOffer(
    const IOcrTitleRecognizer &ocr, const BoundedTextMatcher &matcher,
    const std::vector<TitleCandidate> &candidates, const common::Frame &frame,
    const detector::ThreeCardRois &rois) {
  OfferRecognitionOutput output;
  output.ocr_executed = true;
  output.reason = "ocr_executed";
  detector::PixelRoi screen_roi{0U, 0U, frame.width, frame.height};
  const auto crop = detector::CropRawBgraOwning(frame, screen_roi);
  if (!crop.ok()) {
    output.reason = crop.reason;
    return output;
  }
  const auto source_width = crop.value->width;
  auto ocr_crop = DownscaleCrop(*crop.value, kScreenOcrMaxSide);
  const float scale = ocr_crop.width == 0U
                          ? 1.0F
                          : static_cast<float>(source_width) /
                                static_cast<float>(ocr_crop.width);
  const auto ocr_result = ocr.Recognize(ocr_crop);
  std::array<std::optional<ScreenHit>, common::kAugmentCardCount> best{};
  for (const auto &span : ScreenSpans(ocr_result)) {
    auto match = matcher.Match(span.text, candidates);
    if (!match.matched()) {
      continue;
    }
    const float frame_x = span.center_x() * scale;
    const float frame_y = span.center_y() * scale;
    ScreenHit hit{std::move(match), span.text, frame_x, frame_y,
                  Utf8Units(span.text)};
    std::optional<std::size_t> slot;
    float nearest = 0.0F;
    for (std::size_t index = 0U; index < rois.cards.size(); ++index) {
      const auto &card = rois.cards[index];
      const float left = static_cast<float>(card.x);
      const float right = static_cast<float>(card.x + card.width);
      const float top = static_cast<float>(card.y);
      const float bottom = static_cast<float>(card.y + card.height);
      if (frame_x >= left && frame_x <= right && frame_y >= top &&
          frame_y <= bottom) {
        slot = index;
        break;
      }
      const float center = (left + right) * 0.5F;
      const float distance = std::abs(frame_x - center);
      if (!slot.has_value() || distance < nearest) {
        slot = index;
        nearest = distance;
      }
    }
    if (!slot.has_value()) {
      continue;
    }
    if (!best[*slot].has_value() || BetterScreenHit(hit, *best[*slot])) {
      best[*slot] = std::move(hit);
    }
  }
  std::array<std::string, common::kAugmentCardCount> used_ids{};
  for (std::size_t index = 0U; index < best.size(); ++index) {
    if (!best[index].has_value() || !best[index]->match.id.has_value()) {
      continue;
    }
    used_ids[index] = *best[index]->match.id;
  }
  for (std::size_t index = 0U; index < best.size(); ++index) {
    if (!best[index].has_value()) {
      continue;
    }
    const auto &id = used_ids[index];
    if (id.empty()) {
      continue;
    }
    for (std::size_t other = 0U; other < best.size(); ++other) {
      if (other == index || used_ids[other] != id) {
        continue;
      }
      if (BetterScreenHit(*best[index], *best[other])) {
        best[other].reset();
        used_ids[other].clear();
      } else {
        best[index].reset();
        used_ids[index].clear();
        break;
      }
    }
  }

  bool all_matched = true;
  for (std::size_t index = 0U; index < output.cards.size(); ++index) {
    auto &card = output.cards[index];
    if (!best[index].has_value()) {
      all_matched = false;
      card.state = CardRecognitionState::Unknown;
      card.reason = "screen_ocr_no_library_match";
      card.raw_text = ocr_result.raw_text;
      card.backend = ocr_result.backend;
      continue;
    }
    CandidateMatchSummary summary;
    summary.has_match = true;
    summary.best = best[index]->match;
    OcrTextResult span_result = ocr_result;
    span_result.raw_text = best[index]->raw;
    ApplyMatchToCard(card, span_result, summary);
    card.reason.append(";screen_line");
  }
  if (all_matched) {
    output.reason = "screen_ocr_library_match";
  }
  return output;
}

[[nodiscard]] bool AllCardsRecognized(
    const OfferRecognitionOutput &output) noexcept {
  return std::all_of(output.cards.begin(), output.cards.end(),
                     [](const CardRecognitionOutput &card) {
                       return card.state == CardRecognitionState::Recognized &&
                              card.augment_id.has_value();
                     });
}

[[nodiscard]] bool CardRecognized(
    const CardRecognitionOutput &card) noexcept {
  return card.state == CardRecognitionState::Recognized &&
         card.augment_id.has_value();
}

void MergeRecognizedCards(OfferRecognitionOutput &dest,
                          OfferRecognitionOutput &src) {
  for (std::size_t index = 0U; index < dest.cards.size(); ++index) {
    if (CardRecognized(dest.cards[index]) ||
        !CardRecognized(src.cards[index])) {
      continue;
    }
    dest.cards[index] = std::move(src.cards[index]);
  }
}

void RecognizePrimaryCardSlots(const IOcrTitleRecognizer &ocr,
                               const BoundedTextMatcher &matcher,
                               const std::vector<TitleCandidate> &candidates,
                               const common::Frame &frame,
                               const detector::ThreeCardRois &rois,
                               OfferRecognitionOutput &output) {
  for (std::size_t index = 0U; index < output.cards.size(); ++index) {
    if (CardRecognized(output.cards[index])) {
      continue;
    }
    // Prefer the name-plate crop (blue). The full card ROI is presence only.
    output.cards[index] = RecognizeTitleCrop(ocr, matcher, candidates, frame,
                                             rois.cards[index]);
  }
}

void RecognizeAlternateCardSlots(const IOcrTitleRecognizer &ocr,
                                 const BoundedTextMatcher &matcher,
                                 const std::vector<TitleCandidate> &candidates,
                                 const common::Frame &frame,
                                 const detector::ThreeCardRois &rois,
                                 OfferRecognitionOutput &output) {
  for (std::size_t index = 0U; index < output.cards.size(); ++index) {
    auto &card = output.cards[index];
    if (CardRecognized(card) || !NeedsAlternateTitleBand(card)) {
      continue;
    }
    const auto &card_roi = rois.cards[index];
    const auto &primary = card_roi;
    const auto title_band = FindGoldTitleBand(frame, card_roi);
    const auto alternate =
        title_band.has_value() &&
                title_band->PrimaryCrop() != primary.PrimaryCrop()
            ? title_band
            : TitleBandWithinCard(card_roi, 0.36, 0.26, frame.width,
                                  frame.height);
    if (!alternate.has_value() ||
        alternate->PrimaryCrop() == primary.PrimaryCrop()) {
      continue;
    }
    auto retried =
        RecognizeTitleCrop(ocr, matcher, candidates, frame, *alternate);
    if (retried.state == CardRecognitionState::Recognized ||
        (card.raw_text.empty() && !retried.raw_text.empty())) {
      card = std::move(retried);
    }
  }
}

} // namespace

AugmentRecognitionPipeline::AugmentRecognitionPipeline(
    const IOcrTitleRecognizer &ocr, std::vector<TitleCandidate> candidates,
    const TextMatcherConfig matcher_config)
    : ocr_(ocr), candidates_(std::move(candidates)), matcher_(matcher_config) {}

OfferRecognitionOutput AugmentRecognitionPipeline::Recognize(
    const common::Frame &frame,
    const detector::DetectorResult &stable_detection) const {
  OfferRecognitionOutput output;
  if (!stable_detection.visible || !stable_detection.rois.has_value() ||
      stable_detection.reason != "stable_visible" ||
      stable_detection.frame_id != frame.frame_id) {
    output.reason = stable_detection.frame_id != frame.frame_id
                        ? "stale_detector_result"
                        : "detector_not_stable";
    for (auto &card : output.cards) {
      card.reason = output.reason;
    }
    return output;
  }

  output.ocr_executed = true;
  output.reason = "ocr_executed";
  const auto &rois = *stable_detection.rois;
  const bool allow_screen = frame.width >= kScreenOcrMinWidth &&
                            frame.height >= kScreenOcrMinHeight;
  // Title crops first: live 1080p full-screen OCR is much slower than three
  // small title rectangles. Only retry an alternate band after the cheap
  // path, and only for slots that still look empty.
  RecognizePrimaryCardSlots(ocr_, matcher_, candidates_, frame, rois, output);
  if (AllCardsRecognized(output)) {
    return output;
  }
  if (allow_screen) {
    auto screen =
        RecognizeScreenOffer(ocr_, matcher_, candidates_, frame, rois);
    if (AllCardsRecognized(screen)) {
      return screen;
    }
    MergeRecognizedCards(output, screen);
    if (AllCardsRecognized(output)) {
      return output;
    }
  }
  RecognizeAlternateCardSlots(ocr_, matcher_, candidates_, frame, rois,
                              output);
  return output;
}

} // namespace lol_assistant::vision
