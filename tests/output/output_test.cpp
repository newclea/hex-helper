#include "lol_assistant/output/debug_artifact_writer.h"
#include "lol_assistant/output/debug_preview_window.h"
#include "lol_assistant/output/structured_json_writer.h"
#include "lol_assistant/replay/wic_image_codec.h"

#include <Windows.h>

#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>

#ifndef LOL_TEST_OUTPUT_ROOT
#error LOL_TEST_OUTPUT_ROOT must point inside outputs/tmp/replay_output_worker
#endif

namespace {

int g_failures = 0;
int g_tests = 0;

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

[[nodiscard]] lol_assistant::common::Frame MakeFrame() {
  using namespace lol_assistant::common;
  Frame frame;
  frame.source = FrameSource{FrameSourceKind::Replay, "output-test-frame"};
  frame.frame_id = 42U;
  frame.width = 8U;
  frame.height = 4U;
  frame.stride = frame.width *
                 static_cast<std::uint32_t>(Frame::kBytesPerPixel);
  frame.buffer.resize(static_cast<std::size_t>(frame.stride) * frame.height);
  for (std::uint32_t y = 0U; y < frame.height; ++y) {
    for (std::uint32_t x = 0U; x < frame.width; ++x) {
      const std::size_t offset = static_cast<std::size_t>(y) * frame.stride +
                                 static_cast<std::size_t>(x) * 4U;
      frame.buffer[offset] = static_cast<std::uint8_t>(x * 20U + y);
      frame.buffer[offset + 1U] = static_cast<std::uint8_t>(y * 30U);
      frame.buffer[offset + 2U] = static_cast<std::uint8_t>(255U - x * 10U);
      frame.buffer[offset + 3U] = 255U;
    }
  }
  if (!frame.IsValid()) {
    throw std::runtime_error("Test generated an invalid frame");
  }
  return frame;
}

void WriteText(const std::filesystem::path& path, const std::string& text) {
  std::filesystem::create_directories(path.parent_path());
  std::ofstream output(path, std::ios::binary);
  if (!output) {
    throw std::runtime_error("Unable to create test JSON: " + path.string());
  }
  output.write(text.data(), static_cast<std::streamsize>(text.size()));
}

[[nodiscard]] std::string ReadText(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    throw std::runtime_error("Unable to read test file: " + path.string());
  }
  return {std::istreambuf_iterator<char>(input),
          std::istreambuf_iterator<char>()};
}

[[nodiscard]] std::array<lol_assistant::common::CardSlot,
                         lol_assistant::common::kAugmentCardCount>
MakeCardSlots() {
  using namespace lol_assistant::common;
  return {CardSlot{CardSlotId::Left, NormalizedRoi{0.0, 0.0, 0.25, 1.0}},
          CardSlot{CardSlotId::Center,
                   NormalizedRoi{0.25, 0.0, 0.25, 1.0}},
          CardSlot{CardSlotId::Right,
                   NormalizedRoi{0.5, 0.0, 0.25, 1.0}}};
}

[[nodiscard]] lol_assistant::common::AugmentScreenDetection MakeDetection() {
  using namespace lol_assistant::common;
  AugmentScreenDetection detection;
  detection.state = DetectionState::Detected;
  detection.offer_roi = NormalizedRoi{0.0, 0.0, 0.75, 1.0};
  const auto slots = MakeCardSlots();
  for (std::size_t index = 0U; index < slots.size(); ++index) {
    detection.card_slots[index] = slots[index];
  }
  detection.metadata.source = "unit-detector";
  detection.metadata.confidence = Confidence{0.9F};
  detection.metadata.observed_at = UtcTimestamp::clock::now();
  if (!detection.IsValid()) {
    throw std::runtime_error("Test generated an invalid detection");
  }
  return detection;
}

[[nodiscard]] std::array<
    std::optional<lol_assistant::common::AugmentRecognition>,
    lol_assistant::common::kAugmentCardCount>
MakeRecognitions() {
  using namespace lol_assistant::common;
  std::array<std::optional<AugmentRecognition>, kAugmentCardCount>
      recognitions{};

  AugmentRecognition left;
  left.state = RecognitionState::Recognized;
  left.slot = CardSlotId::Left;
  left.augment_id = "augment.left";
  left.display_name = "强化符文";
  left.metadata.source = "unit-recognizer";
  left.metadata.confidence = Confidence{0.87F};
  left.metadata.observed_at = UtcTimestamp::clock::now();
  recognitions[0U] = left;

  AugmentRecognition center;
  center.state = RecognitionState::NotRecognized;
  center.slot = CardSlotId::Center;
  center.metadata.source = "unit-recognizer";
  center.metadata.confidence = Confidence{0.2F};
  recognitions[1U] = center;

  recognitions[2U] = AugmentRecognition{};
  return recognitions;
}

void TestStructuredJson(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test_name = "structured UTF-8 JSON/null semantics";
  using namespace lol_assistant;

  const auto json_root = root / L"json";
  std::filesystem::create_directories(json_root);

  const common::GameState unknown_state;
  const std::string unknown_json =
      output::StructuredJsonWriter::WriteGameState(unknown_state);
  CHECK(test_name,
        unknown_json.find("\"champion\":null") != std::string::npos);
  CHECK(test_name,
        unknown_json.find("\"offer_round\":null") != std::string::npos);
  CHECK(test_name,
        unknown_json.find("\"current_offer\":null") != std::string::npos);
  WriteText(json_root / L"game_unknown.json", unknown_json);

  const common::AugmentScreenDetection unknown_detection;
  const std::string detection_json =
      output::StructuredJsonWriter::WriteScreenDetection(unknown_detection);
  CHECK(test_name,
        detection_json.find("\"state\":\"UNKNOWN\"") !=
            std::string::npos);
  CHECK(test_name,
        detection_json.find("\"offer_roi\":null") != std::string::npos);
  WriteText(json_root / L"detection_unknown.json", detection_json);

  const auto recognitions = MakeRecognitions();
  const std::string recognitions_json =
      output::StructuredJsonWriter::WriteRecognitions(recognitions);
  CHECK(test_name,
        recognitions_json.find("强化符文") != std::string::npos);
  CHECK(test_name,
        recognitions_json.find("\"augment_id\":null") !=
            std::string::npos);
  WriteText(json_root / L"recognitions.json", recognitions_json);

  common::AugmentOfferObservation offer;
  offer.screen_detection = MakeDetection();
  offer.recognitions = recognitions;
  offer.recognitions[2U].reset();
  offer.metadata.source = "unit-offer";
  offer.metadata.confidence = common::Confidence{0.8F};
  common::GameState known_state;
  known_state.champion = "阿狸";
  known_state.offer_round = 2U;
  known_state.current_offer = offer;
  known_state.metadata.source = "unit-state";
  known_state.metadata.confidence = common::Confidence{0.8F};
  CHECK(test_name, known_state.IsValid());
  WriteText(json_root / L"game_known.json",
            output::StructuredJsonWriter::WriteGameState(known_state));

  common::GameState malformed_utf8;
  malformed_utf8.champion = std::string("\xC3\x28", 2U);
  const std::string sanitized =
      output::StructuredJsonWriter::WriteGameState(malformed_utf8);
  CHECK(test_name,
        sanitized.find("\xEF\xBF\xBD", 0U, 3U) != std::string::npos);
  WriteText(json_root / L"malformed_utf8_sanitized.json", sanitized);
}

[[nodiscard]] bool IsStrictDescendant(const std::filesystem::path& root,
                                      const std::filesystem::path& child) {
  const auto canonical_root = std::filesystem::weakly_canonical(root);
  const auto canonical_child = std::filesystem::weakly_canonical(child);
  auto root_part = canonical_root.begin();
  auto child_part = canonical_child.begin();
  for (; root_part != canonical_root.end(); ++root_part, ++child_part) {
    if (child_part == canonical_child.end() || *root_part != *child_part) {
      return false;
    }
  }
  return child_part != canonical_child.end();
}

void TestArtifactWriter(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test_name = "artifact PNG/sidecar correspondence";
  using namespace lol_assistant;

  const common::Frame frame = MakeFrame();
  output::DebugArtifactRequest request;
  request.session_id = "../../session/雪";
  request.timestamp = common::UtcTimestamp::clock::now();
  request.confidence = common::Confidence{0.92F};
  request.backend = "unit-backend";
  request.catalog_version = "catalog-2026.08";
  request.trigger = output::DebugArtifactTrigger::Manual;
  request.card_slots = MakeCardSlots();

  const auto artifact_root = root / L"artifacts";
  output::DebugArtifactWriter writer(artifact_root);
  output::DefaultDebugArtifactPolicy policy(0.5F, true, true);
  const auto result = writer.WriteIfRequested(frame, request, policy);
  CHECK(test_name, result.has_value());
  if (!result) {
    return;
  }
  CHECK(test_name, std::filesystem::is_regular_file(result->raw_frame));
  CHECK(test_name, std::filesystem::is_regular_file(result->sidecar_json));
  CHECK(test_name, IsStrictDescendant(writer.OutputRoot(), result->raw_frame));
  CHECK(test_name,
        IsStrictDescendant(writer.OutputRoot(), result->sidecar_json));
  for (const auto& roi_path : result->card_rois) {
    CHECK(test_name, std::filesystem::is_regular_file(roi_path));
    CHECK(test_name, IsStrictDescendant(writer.OutputRoot(), roi_path));
  }

  const auto decoded_raw = replay::WicImageCodec::Decode(
      result->raw_frame, frame.source, frame.frame_id);
  CHECK(test_name, decoded_raw.width == frame.width);
  CHECK(test_name, decoded_raw.height == frame.height);
  CHECK(test_name, decoded_raw.buffer == frame.buffer);
  constexpr std::array<std::uint8_t, 3U> expected_blue{0U, 40U, 80U};
  for (std::size_t index = 0U; index < result->card_rois.size(); ++index) {
    const auto roi = replay::WicImageCodec::Decode(
        result->card_rois[index], frame.source, frame.frame_id);
    CHECK(test_name, roi.width == 2U);
    CHECK(test_name, roi.height == frame.height);
    CHECK(test_name, roi.buffer.front() == expected_blue[index]);
  }

  const std::string sidecar = ReadText(result->sidecar_json);
  CHECK(test_name, sidecar.find("\"session_id\":\"../../session/雪\"") !=
                       std::string::npos);
  CHECK(test_name, sidecar.find("\"backend\":\"unit-backend\"") !=
                       std::string::npos);
  CHECK(test_name, sidecar.find("\"catalog_version\":\"catalog-2026.08\"") !=
                       std::string::npos);
  CHECK(test_name,
        sidecar.find(result->raw_frame.filename().string()) !=
            std::string::npos);

  request.trigger = output::DebugArtifactTrigger::LowConfidence;
  CHECK(test_name, !writer.WriteIfRequested(frame, request, policy).has_value());
  request.confidence = common::Confidence{0.2F};
  const auto low_confidence = writer.WriteIfRequested(frame, request, policy);
  CHECK(test_name, low_confidence.has_value());
  CHECK(test_name,
        policy.ShouldPersist({output::DebugArtifactTrigger::Conflict,
                              common::Confidence{1.0F}}));
}

void TestArtifactValidation(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test_name = "artifact request validation";
  using namespace lol_assistant;
  output::DebugArtifactWriter writer(root / L"artifact_validation");
  output::DefaultDebugArtifactPolicy policy;
  output::DebugArtifactRequest request;
  request.session_id = "session";
  request.backend = "backend";
  request.catalog_version = "catalog";
  request.card_slots = MakeCardSlots();
  request.card_slots[2U] = request.card_slots[0U];
  bool rejected = false;
  try {
    static_cast<void>(writer.WriteIfRequested(MakeFrame(), request, policy));
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  CHECK(test_name, rejected);
}

void TestPreviewSmoke() {
  ++g_tests;
  constexpr std::string_view test_name = "two-second Win32 preview smoke";
  using namespace lol_assistant;

  output::DebugPreviewWindow preview("Replay Debug Preview Smoke", 640, 360);
  CHECK(test_name, preview.Create());
  if (!preview.IsOpen()) {
    return;
  }
  output::DebugPreviewOverlay overlay;
  overlay.detector_roi = common::NormalizedRoi{0.0, 0.0, 0.75, 1.0};
  const auto slots = MakeCardSlots();
  for (std::size_t index = 0U; index < slots.size(); ++index) {
    overlay.card_rois[index] = slots[index];
  }
  overlay.fps = 60.0;
  overlay.status = "REPLAY / smoke / 不捕获游戏";
  preview.UpdateFrame(MakeFrame(), overlay);

  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::seconds{2};
  while (std::chrono::steady_clock::now() < deadline) {
    CHECK(test_name, preview.PumpMessages());
    if (!preview.IsOpen()) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds{10});
  }
  preview.Close();
  CHECK(test_name, !preview.IsOpen());
}

}  // namespace

int main() {
  try {
    const std::filesystem::path root =
        std::filesystem::path(LOL_TEST_OUTPUT_ROOT) /
        (L"output_cases_" + std::to_wstring(GetCurrentProcessId()));
    std::filesystem::create_directories(root);
    TestStructuredJson(root);
    TestArtifactWriter(root);
    TestArtifactValidation(root);
    TestPreviewSmoke();
  } catch (const std::exception& error) {
    ++g_failures;
    std::cerr << "[UNCAUGHT] " << error.what() << '\n';
  }

  if (g_failures != 0) {
    std::cerr << g_failures << " output check(s) failed across " << g_tests
              << " test cases.\n";
    return 1;
  }
  std::cout << "[PASS] " << g_tests
            << " output test cases, including the 2-second preview smoke.\n";
  return 0;
}
