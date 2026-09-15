#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "lol_assistant/common/frame.h"
#include "lol_assistant/detector/augment_screen_detector.h"
#include "lol_assistant/vision/icon_matcher.h"
#include "lol_assistant/vision/ocr.h"
#include "lol_assistant/vision/text_matcher.h"

namespace lol_assistant::vision {

enum class CardRecognitionState : std::uint8_t {
  SkippedDetectorUnstable = 0,
  BackendUnavailable = 1,
  OcrFailed = 2,
  Unknown = 3,
  Recognized = 4,
};

struct CardRecognitionOutput final {
  CardRecognitionState state{CardRecognitionState::SkippedDetectorUnstable};
  std::string raw_text{};
  std::string backend{};
  std::optional<float> ocr_confidence{};
  TextMatchKind match_kind{TextMatchKind::Unknown};
  std::string normalized_text{};
  // Lexical similarity from BoundedTextMatcher; this is not a probability.
  float match_confidence{0.0F};
  float match_top2_score{0.0F};
  float match_margin{0.0F};
  // A non-calibrated ranking heuristic until independent calibration/icon
  // evidence exists. Callers must not interpret it as a success probability.
  float final_confidence{0.0F};
  std::optional<std::string> augment_id{};
  std::optional<std::string> display_name{};
  IconMatchResult icon_match{};
  std::string reason{"detector_not_stable"};
};

struct OfferRecognitionOutput final {
  bool ocr_executed{false};
  std::array<CardRecognitionOutput, common::kAugmentCardCount> cards{};
  std::string reason{"detector_not_stable"};
};

class AugmentRecognitionPipeline final {
public:
  AugmentRecognitionPipeline(const IOcrTitleRecognizer &ocr,
                             std::vector<TitleCandidate> candidates,
                             TextMatcherConfig matcher_config = {});

  [[nodiscard]] OfferRecognitionOutput
  Recognize(const common::Frame &frame,
            const detector::DetectorResult &stable_detection) const;

private:
  const IOcrTitleRecognizer &ocr_;
  std::vector<TitleCandidate> candidates_{};
  BoundedTextMatcher matcher_;
};

} // namespace lol_assistant::vision
