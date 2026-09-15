#include "lol_assistant/vision/recognition_pipeline.h"

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

int g_failures = 0;
int g_checks = 0;

void Check(const bool condition, const std::string_view expression,
           const std::string_view test) {
  ++g_checks;
  if (!condition) {
    ++g_failures;
    std::cerr << "[FAIL] " << test << ": " << expression << '\n';
  }
}

#define CHECK(test, expression) Check((expression), #expression, (test))

class FakeOcr final : public lol_assistant::vision::IOcrTitleRecognizer {
public:
  FakeOcr() = default;
  explicit FakeOcr(lol_assistant::vision::OcrTextResult result)
      : result_(std::move(result)) {}
  FakeOcr(lol_assistant::vision::OcrTextResult result,
          const std::uint32_t min_width, const std::uint32_t min_height)
      : result_(std::move(result)), min_width_(min_width),
        min_height_(min_height) {}

  [[nodiscard]] lol_assistant::vision::OcrBackendStatus
  Probe() const noexcept override {
    return {lol_assistant::vision::OcrBackendState::Available, "fake-ocr",
            "available"};
  }

  [[nodiscard]] lol_assistant::vision::OcrTextResult Recognize(
      const lol_assistant::detector::OwningBgraCrop &crop) const noexcept override {
    ++calls;
    if ((min_width_ > 0U || min_height_ > 0U) &&
        (crop.width < min_width_ || crop.height < min_height_)) {
      return {lol_assistant::vision::OcrResultState::Success,
              {},
              "fake-ocr",
              std::nullopt,
              "empty_title_crop"};
    }
    return result_;
  }

  mutable std::uint32_t calls{0U};

private:
  lol_assistant::vision::OcrTextResult result_{
      lol_assistant::vision::OcrResultState::Success, "全心为你", "fake-ocr",
      0.80F, "recognized"};
  std::uint32_t min_width_{0U};
  std::uint32_t min_height_{0U};
};

[[nodiscard]] lol_assistant::common::Frame Frame() {
  lol_assistant::common::Frame frame;
  frame.source = {lol_assistant::common::FrameSourceKind::Replay,
                  "pipeline-synthetic"};
  frame.frame_id = 42U;
  frame.width = 320U;
  frame.height = 180U;
  frame.stride = frame.width * 4U;
  frame.buffer.resize(static_cast<std::size_t>(frame.stride) * frame.height,
                      255U);
  return frame;
}

[[nodiscard]] lol_assistant::detector::NormalizedThreeCardLayout Layout() {
  using lol_assistant::common::NormalizedRoi;
  return {NormalizedRoi{0.10, 0.10, 0.80, 0.80},
          {NormalizedRoi{0.15, 0.20, 0.20, 0.55},
           NormalizedRoi{0.40, 0.20, 0.20, 0.55},
           NormalizedRoi{0.65, 0.20, 0.20, 0.55}}};
}

void TestOcrStableGateAndOutputs() {
  constexpr std::string_view test = "OCR executes only after stable detector";
  auto frame = Frame();
  const auto rois = lol_assistant::detector::ComputeThreeCardRois(
      frame.width, frame.height, Layout());
  CHECK(test, rois.ok());

  FakeOcr ocr;
  const lol_assistant::vision::AugmentRecognitionPipeline pipeline{
      ocr, {{"ARAM_AllForYou", "全心为你"}}};
  lol_assistant::detector::DetectorResult detection;
  detection.frame_id = frame.frame_id;
  detection.visible = true;
  detection.confidence = 0.95F;
  detection.reason = "visible";
  detection.rois = rois.value;

  const auto unstable = pipeline.Recognize(frame, detection);
  CHECK(test, !unstable.ocr_executed);
  CHECK(test, ocr.calls == 0U);
  for (const auto &card : unstable.cards) {
    CHECK(test, card.state == lol_assistant::vision::CardRecognitionState::
                                  SkippedDetectorUnstable);
  }

  detection.reason = "stable_visible";
  const auto stable = pipeline.Recognize(frame, detection);
  CHECK(test, stable.ocr_executed);
  CHECK(test, ocr.calls == 3U);
  for (const auto &card : stable.cards) {
    CHECK(test, card.state ==
                    lol_assistant::vision::CardRecognitionState::Recognized);
    CHECK(test, card.raw_text == "全心为你");
    CHECK(test, card.backend == "fake-ocr");
    CHECK(test, card.ocr_confidence == 0.80F);
    CHECK(test, card.match_kind == lol_assistant::vision::TextMatchKind::Exact);
    CHECK(test, card.normalized_text == "全心为你");
    CHECK(test, card.match_confidence == 1.0F);
    CHECK(test, card.match_top2_score == 0.0F);
    CHECK(test, card.match_margin == 1.0F);
    CHECK(test, card.final_confidence == 0.90F);
    CHECK(test, card.augment_id == "ARAM_AllForYou");
    CHECK(test, !card.icon_match.available());
  }

  detection.frame_id = 41U;
  const auto stale = pipeline.Recognize(frame, detection);
  CHECK(test, !stale.ocr_executed);
  CHECK(test, stale.reason == "stale_detector_result");
  CHECK(test, ocr.calls == 3U);
}

[[nodiscard]] lol_assistant::detector::DetectorResult
StableDetection(const lol_assistant::common::Frame &frame) {
  const auto rois = lol_assistant::detector::ComputeThreeCardRois(
      frame.width, frame.height, Layout());
  lol_assistant::detector::DetectorResult detection;
  detection.frame_id = frame.frame_id;
  detection.visible = true;
  detection.confidence = 0.95F;
  detection.reason = "stable_visible";
  detection.rois = rois.value;
  return detection;
}

void TestLineCandidatesSelectTitleAndCombineAdjacentLines() {
  constexpr std::string_view test = "bounded OCR title-line selection";
  const auto frame = Frame();
  const auto detection = StableDetection(frame);

  FakeOcr title_with_description{
      {lol_assistant::vision::OcrResultState::Success,
       "每局开始时获得护盾\n全心为你\n造成伤害时回复生命",
       "fake-ocr",
       std::nullopt,
       "recognized_without_backend_confidence",
       {"每局开始时获得护盾", "全心为你", "造成伤害时回复生命"}}};
  const lol_assistant::vision::AugmentRecognitionPipeline direct_pipeline{
      title_with_description, {{"ARAM_AllForYou", "全心为你"}}};
  const auto direct = direct_pipeline.Recognize(frame, detection);
  for (const auto &card : direct.cards) {
    CHECK(test, card.state ==
                    lol_assistant::vision::CardRecognitionState::Recognized);
    CHECK(test, card.augment_id == "ARAM_AllForYou");
    CHECK(test,
          card.raw_text == "每局开始时获得护盾\n全心为你\n造成伤害时回复生命");
    CHECK(test, card.match_confidence == 1.0F);
    CHECK(test, !card.icon_match.available());
    CHECK(test, card.final_confidence < 1.0F);
    CHECK(test, std::abs(card.final_confidence - 0.90F) < 0.0001F);
  }

  FakeOcr split_title{{lol_assistant::vision::OcrResultState::Success,
                       "全心\n为你\n获得额外属性",
                       "fake-ocr",
                       std::nullopt,
                       "recognized_without_backend_confidence",
                       {"全心", "为你", "获得额外属性"}}};
  const lol_assistant::vision::AugmentRecognitionPipeline split_pipeline{
      split_title, {{"ARAM_AllForYou", "全心为你"}}};
  const auto split = split_pipeline.Recognize(frame, detection);
  for (const auto &card : split.cards) {
    CHECK(test, card.state ==
                    lol_assistant::vision::CardRecognitionState::Recognized);
    CHECK(test, card.augment_id == "ARAM_AllForYou");
    CHECK(test, card.match_confidence == 1.0F);
    CHECK(test, std::abs(card.final_confidence - 0.86F) < 0.0001F);
  }
}

void TestConflictingLineIdsFailClosed() {
  constexpr std::string_view test = "conflicting OCR line IDs fail closed";
  const auto frame = Frame();
  const auto detection = StableDetection(frame);
  FakeOcr conflicting{{lol_assistant::vision::OcrResultState::Success,
                       "全心为你\n星界躯体",
                       "fake-ocr",
                       std::nullopt,
                       "recognized_without_backend_confidence",
                       {"全心为你", "星界躯体"}}};
  const lol_assistant::vision::AugmentRecognitionPipeline pipeline{
      conflicting,
      {{"ARAM_AllForYou", "全心为你"}, {"ARAM_CelestialBody", "星界躯体"}}};
  const auto output = pipeline.Recognize(frame, detection);
  for (const auto &card : output.cards) {
    CHECK(test,
          card.state == lol_assistant::vision::CardRecognitionState::Unknown);
    CHECK(test, !card.augment_id.has_value());
    CHECK(test, card.reason == "conflicting_ocr_candidate_ids");
    CHECK(test, card.final_confidence == 0.0F);
  }
}

void TestNoConfidenceFallbacksAreExplicitHeuristics() {
  constexpr std::string_view test = "no-confidence lexical heuristics";
  const auto frame = Frame();
  const auto detection = StableDetection(frame);

  FakeOcr normalized{{lol_assistant::vision::OcrResultState::Success,
                      " 全心，为你 ",
                      "fake-ocr",
                      std::nullopt,
                      "recognized_without_backend_confidence",
                      {" 全心，为你 "}}};
  const lol_assistant::vision::AugmentRecognitionPipeline normalized_pipeline{
      normalized, {{"ARAM_AllForYou", "全心为你"}}};
  const auto normalized_output =
      normalized_pipeline.Recognize(frame, detection);
  for (const auto &card : normalized_output.cards) {
    CHECK(test, card.match_confidence == 1.0F);
    CHECK(test, std::abs(card.final_confidence - 0.86F) < 0.0001F);
  }

  FakeOcr fuzzy{{lol_assistant::vision::OcrResultState::Success,
                 "全心为伱",
                 "fake-ocr",
                 std::nullopt,
                 "recognized_without_backend_confidence",
                 {"全心为伱"}}};
  const lol_assistant::vision::AugmentRecognitionPipeline fuzzy_pipeline{
      fuzzy, {{"ARAM_AllForYou", "全心为你"}}};
  const auto fuzzy_output = fuzzy_pipeline.Recognize(frame, detection);
  for (const auto &card : fuzzy_output.cards) {
    CHECK(test, card.state ==
                    lol_assistant::vision::CardRecognitionState::Recognized);
    CHECK(test, card.match_confidence < 1.0F);
    CHECK(test, card.match_kind == lol_assistant::vision::TextMatchKind::Fuzzy);
    CHECK(test, card.match_margin > 0.0F);
    CHECK(test, card.final_confidence <= 0.82F);
  }
}

[[nodiscard]] lol_assistant::common::Frame LargeFrame() {
  auto frame = Frame();
  frame.width = 1280U;
  frame.height = 720U;
  frame.stride = frame.width * 4U;
  frame.buffer.assign(static_cast<std::size_t>(frame.stride) * frame.height,
                      255U);
  return frame;
}

void TestFullScreenLineSpansMatchLibrary() {
  constexpr std::string_view test = "full-screen same-line library match";
  const auto frame = LargeFrame();
  const auto detection = StableDetection(frame);
  CHECK(test, detection.rois.has_value());
  const auto span_for_card =
      [](const std::string &text,
         const lol_assistant::detector::PixelRoi &card) {
        return lol_assistant::vision::OcrLineSpan{
            text, static_cast<float>(card.x + card.width / 4U),
            static_cast<float>(card.y + card.height / 8U),
            static_cast<float>(card.width / 2U),
            static_cast<float>(std::max(16U, card.height / 16U))};
      };
  lol_assistant::vision::OcrTextResult screen_result{
      lol_assistant::vision::OcrResultState::Success,
      "利刃华尔兹 功能\n终极唤醒\n终极九头蛇 伤害",
      "fake-ocr",
      std::nullopt,
      "recognized_without_backend_confidence",
      {"利刃华尔兹 功能", "终极唤醒", "终极九头蛇 伤害"},
      {span_for_card("利刃华尔兹", detection.rois->cards[0]),
       span_for_card("终极唤醒", detection.rois->cards[1]),
       span_for_card("终极九头蛇", detection.rois->cards[2])}};
  FakeOcr ocr{std::move(screen_result), 640U, 360U};
  const lol_assistant::vision::AugmentRecognitionPipeline pipeline{
      ocr,
      {{"ARAM_BladeWaltz", "利刃华尔兹"},
       {"UltimateAwakening", "终极唤醒"},
       {"Quest_UltraHydra", "终极九头蛇"}}};
  const auto output = pipeline.Recognize(frame, detection);
  CHECK(test, output.ocr_executed);
  CHECK(test, ocr.calls == 4U);
  CHECK(test, output.reason == "screen_ocr_library_match");
  CHECK(test, output.cards[0].augment_id == "ARAM_BladeWaltz");
  CHECK(test, output.cards[1].augment_id == "UltimateAwakening");
  CHECK(test, output.cards[2].augment_id == "Quest_UltraHydra");
  for (const auto &card : output.cards) {
    CHECK(test, card.state ==
                    lol_assistant::vision::CardRecognitionState::Recognized);
    CHECK(test, card.reason.find("screen_line") != std::string::npos);
  }
}

void TestCardTitleCropsSkipFullScreen() {
  constexpr std::string_view test = "title crops skip full-screen OCR";
  const auto frame = LargeFrame();
  const auto detection = StableDetection(frame);
  FakeOcr ocr;
  const lol_assistant::vision::AugmentRecognitionPipeline pipeline{
      ocr, {{"ARAM_AllForYou", "全心为你"}}};
  const auto output = pipeline.Recognize(frame, detection);
  CHECK(test, output.ocr_executed);
  CHECK(test, ocr.calls == 3U);
  CHECK(test, output.reason == "ocr_executed");
  for (const auto &card : output.cards) {
    CHECK(test, card.state ==
                    lol_assistant::vision::CardRecognitionState::Recognized);
    CHECK(test, card.augment_id == "ARAM_AllForYou");
  }
}

} // namespace

int main() {
  TestOcrStableGateAndOutputs();
  TestLineCandidatesSelectTitleAndCombineAdjacentLines();
  TestConflictingLineIdsFailClosed();
  TestNoConfidenceFallbacksAreExplicitHeuristics();
  TestFullScreenLineSpansMatchLibrary();
  TestCardTitleCropsSkipFullScreen();
  std::cout << "recognition pipeline checks=" << g_checks
            << " failures=" << g_failures << '\n';
  return g_failures == 0 ? 0 : 1;
}
