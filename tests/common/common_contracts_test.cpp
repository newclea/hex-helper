#include "lol_assistant/common/contracts.h"

#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <string_view>

namespace {

int g_failures = 0;

void Check(const bool condition, const std::string_view expression,
           const std::string_view test_name) {
  if (condition) {
    return;
  }
  ++g_failures;
  std::cerr << "[FAIL] " << test_name << ": " << expression << '\n';
}

#define CHECK(test_name, expression) \
  Check((expression), #expression, (test_name))

void TestDefaultUnknownAndNullSemantics() {
  using namespace lol_assistant::common;
  constexpr std::string_view test_name = "default UNKNOWN/null semantics";

  const Frame frame;
  CHECK(test_name, frame.source.kind == FrameSourceKind::Unknown);
  CHECK(test_name, frame.source.id.empty());
  CHECK(test_name, !frame.timestamps.source_timestamp.has_value());
  CHECK(test_name, !frame.timestamps.capture_started.has_value());
  CHECK(test_name, !frame.timestamps.capture_completed.has_value());
  CHECK(test_name, !frame.timestamps.captured_at_utc.has_value());
  CHECK(test_name, frame.buffer.empty());

  const AugmentScreenDetection detection;
  CHECK(test_name, detection.state == DetectionState::Unknown);
  CHECK(test_name, !detection.offer_roi.has_value());
  for (const auto& slot : detection.card_slots) {
    CHECK(test_name, !slot.has_value());
  }
  CHECK(test_name, detection.metadata.source == kUnknownObservationSource);
  CHECK(test_name, detection.metadata.confidence.value == 0.0F);
  CHECK(test_name, !detection.metadata.observed_at.has_value());

  const AugmentRecognition recognition;
  CHECK(test_name, recognition.state == RecognitionState::Unknown);
  CHECK(test_name, recognition.slot == CardSlotId::Unknown);
  CHECK(test_name, !recognition.augment_id.has_value());
  CHECK(test_name, !recognition.display_name.has_value());

  const AugmentOfferObservation offer;
  CHECK(test_name, !offer.screen_detection.has_value());
  for (const auto& item : offer.recognitions) {
    CHECK(test_name, !item.has_value());
  }

  const GameState state;
  CHECK(test_name, !state.champion.has_value());
  CHECK(test_name, !state.offer_round.has_value());
  CHECK(test_name, state.selected_augments.empty());
  CHECK(test_name, !state.current_offer.has_value());
  CHECK(test_name, state.IsValid());
}

void TestNormalizedRoiValidation() {
  using lol_assistant::common::NormalizedRoi;
  constexpr std::string_view test_name = "normalized ROI validation";

  CHECK(test_name, (NormalizedRoi{0.0, 0.0, 1.0, 1.0}.IsValid()));
  CHECK(test_name, (NormalizedRoi{0.1, 0.2, 0.3, 0.4}.IsValid()));
  CHECK(test_name, !(NormalizedRoi{-0.1, 0.0, 0.2, 0.2}.IsValid()));
  CHECK(test_name, !(NormalizedRoi{0.0, 0.0, 0.0, 0.2}.IsValid()));
  CHECK(test_name, !(NormalizedRoi{0.8, 0.0, 0.3, 0.2}.IsValid()));
  CHECK(test_name,
        !(NormalizedRoi{0.0, 0.0, std::numeric_limits<double>::quiet_NaN(),
                         0.2}
              .IsValid()));
}

void TestFrameBufferSizeValidation() {
  using namespace lol_assistant::common;
  constexpr std::string_view test_name = "Frame buffer size validation";

  Frame frame;
  frame.source = FrameSource{FrameSourceKind::Replay, "unit-test-replay"};
  frame.frame_id = 1U;
  frame.width = 2U;
  frame.height = 2U;
  frame.stride = 8U;
  frame.buffer.resize(16U);
  CHECK(test_name, frame.RequiredBufferSize() == 16U);
  CHECK(test_name, frame.IsValid());

  frame.buffer.resize(15U);
  CHECK(test_name, !frame.IsValid());

  frame.buffer.resize(16U);
  frame.stride = 7U;
  CHECK(test_name, !frame.RequiredBufferSize().has_value());
  CHECK(test_name, !frame.IsValid());

  frame.stride = 12U;
  frame.buffer.resize(24U);
  CHECK(test_name, frame.IsValid());

  const auto now = MonotonicTimestamp::clock::now();
  frame.timestamps.capture_started = now;
  frame.timestamps.capture_completed = now - std::chrono::milliseconds{1};
  CHECK(test_name, !frame.IsValid());
}

void TestConfidenceAndStubProvenance() {
  using namespace lol_assistant::common;
  constexpr std::string_view test_name = "confidence and stub provenance";

  CHECK(test_name, Confidence{0.0F}.IsValid());
  CHECK(test_name, Confidence{1.0F}.IsValid());
  CHECK(test_name, !Confidence{-0.01F}.IsValid());
  CHECK(test_name, !Confidence{1.01F}.IsValid());
  CHECK(test_name,
        !Confidence{std::numeric_limits<float>::quiet_NaN()}.IsValid());

  auto stub = ObservationMetadata::Stub();
  CHECK(test_name, stub.source == kStubObservationSource);
  CHECK(test_name, stub.confidence.value == 0.0F);
  CHECK(test_name, !stub.observed_at.has_value());
  CHECK(test_name, stub.IsValid());

  stub.confidence = Confidence{0.5F};
  CHECK(test_name, !stub.IsValid());

  const FrameSource stub_frame_source{FrameSourceKind::Stub, "stub"};
  CHECK(test_name, stub_frame_source.IsValid());
  const FrameSource dishonest_stub{FrameSourceKind::Stub, "capture"};
  CHECK(test_name, !dishonest_stub.IsValid());
}

}  // namespace

int main() {
  TestDefaultUnknownAndNullSemantics();
  TestNormalizedRoiValidation();
  TestFrameBufferSizeValidation();
  TestConfidenceAndStubProvenance();

  if (g_failures != 0) {
    std::cerr << g_failures << " contract check(s) failed.\n";
    return 1;
  }

  std::cout << "All common contract checks passed.\n";
  return 0;
}
