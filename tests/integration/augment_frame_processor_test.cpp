#include "augment_frame_processor.h"

#include <Windows.h>
#include <winrt/base.h>
#include <winsqlite/winsqlite3.h>

#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "lol_assistant/knowledge/augment_catalog.h"
#include "lol_assistant/vision/text_matcher.h"

#ifndef LOL_FRAME_PROCESSOR_RUNTIME_ROOT
#error LOL_FRAME_PROCESSOR_RUNTIME_ROOT must point to outputs/runtime
#endif

#ifndef LOL_ASSISTANT_CATALOG_PATH
#error LOL_ASSISTANT_CATALOG_PATH must point to the real augment catalog
#endif

namespace {

using lol_assistant::app::AcceptedOfferResult;
using lol_assistant::app::AugmentFrameProcessor;
using lol_assistant::app::FrameProcessOptions;
using lol_assistant::app::FrameProcessResult;
using lol_assistant::app::IOfferSessionRuntime;
using lol_assistant::app::Phase1SessionRuntime;
using lol_assistant::app::SessionRuntimeError;

void Require(const bool condition, const std::string_view message) {
  if (!condition) {
    throw std::runtime_error(std::string{message});
  }
}

[[nodiscard]] std::string PathUtf8(const std::filesystem::path& path) {
  const auto bytes = path.generic_u8string();
  return {bytes.begin(), bytes.end()};
}

[[nodiscard]] std::filesystem::path TestRuntimeRoot() {
  return std::filesystem::path{LOL_FRAME_PROCESSOR_RUNTIME_ROOT}.parent_path() /
         L"tmp" / L"fix_processor" / L"runtime";
}

class RawDatabase final {
 public:
  explicit RawDatabase(const std::filesystem::path& path) {
    const std::string path_utf8 = PathUtf8(path);
    const int status =
        sqlite3_open_v2(path_utf8.c_str(), &database_,
                        SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX, nullptr);
    if (status != SQLITE_OK) {
      throw std::runtime_error("failed to open frame processor SQLite output");
    }
  }

  ~RawDatabase() {
    if (database_ != nullptr) {
      (void)sqlite3_close(database_);
    }
  }

  RawDatabase(const RawDatabase&) = delete;
  RawDatabase& operator=(const RawDatabase&) = delete;

  [[nodiscard]] std::int64_t ScalarInt(const std::string_view sql) const {
    sqlite3_stmt* statement = nullptr;
    const std::string query{sql};
    if (sqlite3_prepare_v2(database_, query.c_str(), -1, &statement, nullptr) !=
        SQLITE_OK) {
      throw std::runtime_error("failed to prepare frame processor query");
    }
    if (sqlite3_step(statement) != SQLITE_ROW) {
      (void)sqlite3_finalize(statement);
      throw std::runtime_error("frame processor query returned no row");
    }
    const auto value = sqlite3_column_int64(statement, 0);
    (void)sqlite3_finalize(statement);
    return value;
  }

 private:
  sqlite3* database_{nullptr};
};

enum class ScriptedOcrState : std::uint8_t {
  Success = 0,
  BackendUnavailable,
  Failed,
};

struct OcrScan final {
  ScriptedOcrState state{ScriptedOcrState::Success};
  std::array<std::string, lol_assistant::common::kAugmentCardCount> titles{};
  std::optional<float> confidence{0.96F};
};

[[nodiscard]] OcrScan SuccessScan(std::string left, std::string center,
                                  std::string right,
                                  const float confidence = 0.96F) {
  return {ScriptedOcrState::Success,
          {std::move(left), std::move(center), std::move(right)},
          confidence};
}

[[nodiscard]] OcrScan BackendUnavailableScan() {
  return {ScriptedOcrState::BackendUnavailable, {}, std::nullopt};
}

[[nodiscard]] OcrScan FailedScan() {
  return {ScriptedOcrState::Failed, {}, std::nullopt};
}

[[nodiscard]] OcrScan UnknownScan() {
  return SuccessScan("NoSuchAlpha", "NoSuchBeta", "NoSuchGamma");
}

class ScriptedOcr final : public lol_assistant::vision::IOcrTitleRecognizer {
 public:
  explicit ScriptedOcr(std::vector<OcrScan> scans) : scans_(std::move(scans)) {
    if (scans_.empty()) {
      throw std::invalid_argument("OCR script must contain at least one scan");
    }
  }

  [[nodiscard]] lol_assistant::vision::OcrBackendStatus Probe()
      const noexcept override {
    return {lol_assistant::vision::OcrBackendState::Available,
            "frame-processor-scripted-ocr", "available"};
  }

  [[nodiscard]] lol_assistant::vision::OcrTextResult Recognize(
      const lol_assistant::detector::OwningBgraCrop& crop)
      const noexcept override {
    const std::size_t call = calls_++;
    if (call < crop_sizes_.size()) {
      crop_sizes_[call] = {crop.width, crop.height};
    }
    const std::size_t scan_index = std::min(
        call / lol_assistant::common::kAugmentCardCount, scans_.size() - 1U);
    const std::size_t slot = call % lol_assistant::common::kAugmentCardCount;
    const auto& scan = scans_[scan_index];

    lol_assistant::vision::OcrTextResult result;
    result.backend = "frame-processor-scripted-ocr";
    result.ocr_confidence = scan.confidence;
    if (scan.state == ScriptedOcrState::BackendUnavailable) {
      result.state = lol_assistant::vision::OcrResultState::BackendUnavailable;
      result.reason = "backend_unavailable:test";
      return result;
    }
    if (scan.state == ScriptedOcrState::Failed) {
      result.state = lol_assistant::vision::OcrResultState::RecognitionFailed;
      result.reason = "recognition_failed:test";
      return result;
    }
    result.state = lol_assistant::vision::OcrResultState::Success;
    result.raw_text = scan.titles[slot];
    result.reason = "recognized:test";
    return result;
  }

  [[nodiscard]] std::size_t calls() const noexcept { return calls_; }

  [[nodiscard]] std::pair<std::uint32_t, std::uint32_t> crop_size(
      const std::size_t call) const noexcept {
    return call < crop_sizes_.size()
               ? crop_sizes_[call]
               : std::pair<std::uint32_t, std::uint32_t>{0U, 0U};
  }

 private:
  std::vector<OcrScan> scans_{};
  mutable std::size_t calls_{0U};
  mutable std::array<std::pair<std::uint32_t, std::uint32_t>, 64U>
      crop_sizes_{};
};

class FaultInjectingRuntime final : public IOfferSessionRuntime {
 public:
  explicit FaultInjectingRuntime(
      std::vector<SessionRuntimeError> scripted_results =
          {SessionRuntimeError::Ok})
      : scripted_results_(std::move(scripted_results)) {}

  [[nodiscard]] bool IsOpen() const noexcept override { return true; }

  [[nodiscard]] AcceptedOfferResult AcceptOffer(
      const lol_assistant::common::GameState& state,
      const std::array<lol_assistant::vision::CardRecognitionOutput,
                       lol_assistant::common::kAugmentCardCount>& card_outputs,
      const lol_assistant::common::Frame&,
      const lol_assistant::detector::ThreeCardRois&,
      std::optional<std::string> selected_augment_id) override {
    states.push_back(state);
    attempted_cards.push_back(card_outputs);
    attempted_selections.push_back(selected_augment_id);
    const auto index = std::min(calls_, scripted_results_.size() - 1U);
    const auto code = scripted_results_[index];
    ++calls_;

    AcceptedOfferResult result;
    result.status.code = code;
    result.status.message = code == SessionRuntimeError::Ok
                                ? "accepted"
                                : "fault_injected_before_commit";
    if (code == SessionRuntimeError::Ok) {
      result.accepted = true;
      result.stdout_json = "{\"accepted\":true}";
      persisted_selections.push_back(std::move(selected_augment_id));
    }
    return result;
  }

  [[nodiscard]] std::size_t calls() const noexcept { return calls_; }

  std::vector<lol_assistant::common::GameState> states{};
  std::vector<std::array<lol_assistant::vision::CardRecognitionOutput,
                         lol_assistant::common::kAugmentCardCount>>
      attempted_cards{};
  std::vector<std::optional<std::string>> attempted_selections{};
  std::vector<std::optional<std::string>> persisted_selections{};

 private:
  std::vector<SessionRuntimeError> scripted_results_{};
  std::size_t calls_{0U};
};

[[nodiscard]] lol_assistant::detector::NormalizedThreeCardLayout Layout() {
  using lol_assistant::common::NormalizedRoi;
  return {NormalizedRoi{0.10, 0.10, 0.80, 0.80},
          {NormalizedRoi{0.15, 0.20, 0.20, 0.55},
           NormalizedRoi{0.40, 0.20, 0.20, 0.55},
           NormalizedRoi{0.65, 0.20, 0.20, 0.55}}};
}

[[nodiscard]] lol_assistant::detector::AugmentScreenDetectorConfig
DetectorConfig() {
  lol_assistant::detector::AugmentScreenDetectorConfig config;
  config.layout = Layout();
  config.sample_step = 1U;
  return config;
}

void PaintSyntheticCards(lol_assistant::common::Frame& frame,
                         const lol_assistant::detector::ThreeCardRois& rois,
                         const std::uint32_t pattern) {
  for (const auto& roi : rois.cards) {
    for (std::uint32_t y = 0U; y < roi.height; ++y) {
      for (std::uint32_t x = 0U; x < roi.width; ++x) {
        const bool high = ((x / 4U + y / 4U + pattern) % 2U) != 0U;
        const std::uint8_t value =
            high ? std::uint8_t{220U} : std::uint8_t{45U};
        const auto offset = static_cast<std::size_t>(roi.y + y) * frame.stride +
                            static_cast<std::size_t>(roi.x + x) * 4U;
        frame.buffer[offset] = value;
        frame.buffer[offset + 1U] = value;
        frame.buffer[offset + 2U] = value;
        frame.buffer[offset + 3U] = 255U;
      }
    }
  }
}

void PaintDistinctSyntheticIcons(
    lol_assistant::common::Frame& frame,
    const lol_assistant::detector::ThreeCardRois& rois) {
  for (std::size_t slot = 0U; slot < rois.icon_rects.size(); ++slot) {
    const auto& roi = rois.icon_rects[slot];
    for (std::uint32_t y = 0U; y < roi.height; ++y) {
      for (std::uint32_t x = 0U; x < roi.width; ++x) {
        std::uint32_t value = 0U;
        if (slot == 0U) {
          value = 20U + (200U * x) / std::max(1U, roi.width - 1U);
        } else if (slot == 1U) {
          value = 220U - (200U * x) / std::max(1U, roi.width - 1U);
        } else {
          const auto band = (8U * x + std::max(1U, roi.width - 1U) / 2U) /
                            std::max(1U, roi.width - 1U);
          value = (band % 2U) == 0U ? 20U : 220U;
        }
        const auto offset = static_cast<std::size_t>(roi.y + y) * frame.stride +
                            static_cast<std::size_t>(roi.x + x) * 4U;
        frame.buffer[offset + 0U] = static_cast<std::uint8_t>(value);
        frame.buffer[offset + 1U] = static_cast<std::uint8_t>(value);
        frame.buffer[offset + 2U] = static_cast<std::uint8_t>(value);
        frame.buffer[offset + 3U] = 255U;
      }
    }
  }
}

[[nodiscard]] lol_assistant::common::Frame MakeFrame(
    const std::uint64_t frame_id, const bool visible,
    const std::uint32_t pattern = 0U, const bool distinct_icons = false) {
  using namespace lol_assistant::common;
  Frame frame;
  frame.source = {FrameSourceKind::Replay, "frame-processor-synthetic"};
  frame.frame_id = frame_id;
  frame.timestamps.captured_at_utc =
      UtcTimestamp::clock::now() + std::chrono::microseconds{frame_id};
  frame.width = 320U;
  frame.height = 180U;
  frame.stride =
      frame.width * static_cast<std::uint32_t>(Frame::kBytesPerPixel);
  frame.buffer.assign(static_cast<std::size_t>(frame.stride) * frame.height,
                      8U);
  for (std::size_t offset = 3U; offset < frame.buffer.size();
       offset += Frame::kBytesPerPixel) {
    frame.buffer[offset] = 255U;
  }
  if (visible) {
    const auto rois = lol_assistant::detector::ComputeThreeCardRois(
        frame.width, frame.height, Layout());
    Require(rois.ok(), "synthetic 16:9 frame ROIs must be valid");
    PaintSyntheticCards(frame, *rois.value, pattern);
    if (distinct_icons) {
      PaintDistinctSyntheticIcons(frame, *rois.value);
    }
  }
  Require(frame.IsValid(), "synthetic frame must satisfy Frame contract");
  return frame;
}

[[nodiscard]] FrameProcessResult ProcessVisible(
    AugmentFrameProcessor& processor, const std::uint64_t frame_id,
    const std::uint32_t pattern = 0U, const bool distinct_icons = false) {
  return processor.Process(MakeFrame(frame_id, true, pattern, distinct_icons));
}

[[nodiscard]] FrameProcessResult ProcessVisibleForced(
    AugmentFrameProcessor& processor, const std::uint64_t frame_id,
    const std::uint32_t pattern = 0U, const bool distinct_icons = false) {
  return processor.Process(MakeFrame(frame_id, true, pattern, distinct_icons),
                           FrameProcessOptions{true});
}

[[nodiscard]] FrameProcessResult ProcessInvisible(
    AugmentFrameProcessor& processor, const std::uint64_t frame_id) {
  return processor.Process(MakeFrame(frame_id, false));
}

[[nodiscard]] FrameProcessResult ProcessInvisibleForced(
    AugmentFrameProcessor& processor, const std::uint64_t frame_id) {
  return processor.Process(MakeFrame(frame_id, false),
                           FrameProcessOptions{true});
}

[[nodiscard]] std::vector<lol_assistant::vision::TitleCandidate>
SimpleCandidates() {
  return {{"alpha", "AlphaOne"}, {"beta", "BetaTwo"}, {"gamma", "GammaThree"}};
}

[[nodiscard]] std::vector<lol_assistant::vision::TitleCandidate>
TwoRoundCandidates() {
  auto candidates = SimpleCandidates();
  candidates.push_back({"delta", "DeltaFour"});
  candidates.push_back({"epsilon", "EpsilonFive"});
  candidates.push_back({"zeta", "ZetaSix"});
  return candidates;
}

[[nodiscard]] OcrScan ExactSimpleScan() {
  return SuccessScan("AlphaOne", "BetaTwo", "GammaThree");
}

[[nodiscard]] OcrScan ExactSecondRoundScan() {
  return SuccessScan("DeltaFour", "EpsilonFive", "ZetaSix");
}

[[nodiscard]] OcrScan FuzzySimpleScan() {
  return SuccessScan("AlphaOnf", "BetaTwp", "GammaThref");
}

[[nodiscard]] OcrScan NormalizedSimpleScan() {
  return SuccessScan(" Alpha-One ", " Beta Two ", " Gamma, Three ");
}

[[nodiscard]] std::size_t CountSubstring(const std::string_view text,
                                         const std::string_view needle) {
  std::size_t count = 0U;
  std::size_t position = 0U;
  while ((position = text.find(needle, position)) != std::string_view::npos) {
    ++count;
    position += needle.size();
  }
  return count;
}

[[nodiscard]] std::string ReadFile(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    throw std::runtime_error("failed to read frame processor output file");
  }
  return {std::istreambuf_iterator<char>{input},
          std::istreambuf_iterator<char>{}};
}

void ValidatePersistence(const std::filesystem::path& database_path,
                         const std::filesystem::path& jsonl_path,
                         const std::filesystem::path& output_directory) {
  RawDatabase database{database_path};
  Require(database.ScalarInt("SELECT COUNT(*) FROM augment_offers;") == 2,
          "exactly two consensus offers must persist");
  Require(database.ScalarInt(
              "SELECT COUNT(DISTINCT offer_round) FROM augment_offers;") == 2,
          "persisted offers must occupy two explicitly advanced rounds");
  Require(database.ScalarInt("SELECT COUNT(*) FROM recognition_results;") == 6,
          "two offers must persist exactly six recognition rows");
  Require(database.ScalarInt("SELECT COUNT(*) FROM augment_choices;") == 1,
          "session selected slot must be consumed after one persisted choice");
  Require(database.ScalarInt("SELECT COUNT(*) FROM artifacts;") == 10,
          "two offers must persist exactly ten artifact rows");

  const std::string jsonl = ReadFile(jsonl_path);
  Require(CountSubstring(jsonl, "\"event_type\":\"accepted_offer\"") == 2U,
          "JSONL must contain two and only two accepted offers");

  std::size_t png_count = 0U;
  std::size_t sidecar_count = 0U;
  for (const auto& entry :
       std::filesystem::directory_iterator(output_directory)) {
    if (entry.path().extension() == L".png") {
      ++png_count;
    }
    if (entry.path().extension() == L".json") {
      ++sidecar_count;
    }
  }
  Require(png_count == 8U && sidecar_count == 2U,
          "accepted offers must emit exact RAW/ROI/sidecar evidence counts");
}

void TestTwoFrameConsensusRoundAdvanceAndSqlite() {
  using namespace lol_assistant;
  const auto catalog_result = knowledge::LoadAugmentCatalog(
      std::filesystem::path{LOL_ASSISTANT_CATALOG_PATH});
  Require(catalog_result.ok(), "real augment catalog must load");
  auto candidates =
      vision::BuildTitleCandidates(*catalog_result.catalog, "KIWI");
  Require(candidates.size() > 6U, "KIWI candidate set must be mode-aware");

  std::unique_ptr<Phase1SessionRuntime> runtime;
  const auto create_status = Phase1SessionRuntime::Create(
      TestRuntimeRoot(), "阿狸", "KIWI",
      {"frame-processor-integration", "frame-processor-scripted-ocr",
       catalog_result.catalog->catalog_version(), "synthetic-16:9"},
      runtime);
  Require(create_status.IsSuccess() && runtime != nullptr,
          "real SessionRuntime must be created in fix_processor temp root");

  ScriptedOcr ocr{{
      SuccessScan("物理转魔法", "全心为你", "尖端发明家"),
      SuccessScan("大法师", "回归基本功", "狙神飞星"),
  }};
  AugmentFrameProcessor processor{ocr, std::move(candidates), *runtime, "阿狸",
                                  1U,  DetectorConfig()};

  const auto first = ProcessVisible(processor, 1U);
  const auto second = ProcessVisible(processor, 2U);
  const auto consensus_one = ProcessVisible(processor, 3U);
  Require(first.raw_detector.visible && !first.stable_detector.visible &&
              !second.stable_detector.visible && ocr.calls() == 3U,
          "only the third detector-stable frame may begin three-card OCR");
  Require(consensus_one.ocr_executed && !consensus_one.accepted &&
              consensus_one.reason == "awaiting_ocr_consensus:1/2",
          "one lexical frame must remain unaccepted");
  Require(
      consensus_one.rois.has_value() && consensus_one.recognition.has_value(),
      "stable OCR frame must retain ROI and card-recognition debug data");
  for (std::size_t slot = 0U; slot < common::kAugmentCardCount; ++slot) {
    const auto& rois = *consensus_one.rois;
    const auto [ocr_width, ocr_height] = ocr.crop_size(slot);
    const auto title_width = rois.title_rects[slot].width;
    const auto title_height = rois.title_rects[slot].height;
    const std::uint32_t title_scale =
        title_height > 0U && title_height < 48U ? 3U : 2U;
    Require(ocr_width == title_width * title_scale &&
                ocr_height == title_height * title_scale,
            "product OCR must upscale the primary title rectangle");
    Require(rois.cards[slot].width > title_width &&
                rois.cards[slot].height > title_height,
            "full-card ROI must remain available for collector debug output");
    Require(consensus_one.recognition->cards[slot].backend.find(
                "title_preprocess=contrast_gray_") != std::string::npos &&
                consensus_one.recognition->cards[slot].backend.find(
                    ";strategy=single_default") != std::string::npos,
            "product OCR must persist the contrast-scaled title preprocess");
  }

  const auto accepted_one = ProcessVisible(processor, 4U);
  Require(!accepted_one.ocr_executed && accepted_one.accepted &&
              accepted_one.reason == "accepted_offer" && ocr.calls() == 3U,
          "the second stable frame must reuse three-card OCR consensus");
  const auto transient_change = ProcessVisible(processor, 5U, 1U);
  const auto returned_same = ProcessVisible(processor, 6U, 0U);
  Require(!transient_change.ocr_executed &&
              transient_change.reason ==
                  "awaiting_content_change_confirmation:1/2" &&
               !returned_same.ocr_executed && returned_same.duplicate &&
               ocr.calls() == 3U,
          "one-frame content noise must not re-arm or repeat OCR");

  (void)ProcessInvisible(processor, 7U);
  const auto dismissed = ProcessInvisible(processor, 8U);
  Require(dismissed.reason == "screen_dismissed_round_advanced",
          "two invisible frames must emit one explicit round advance");

  (void)ProcessVisible(processor, 9U, 1U);
  (void)ProcessVisible(processor, 10U, 1U);
  const auto consensus_two = ProcessVisible(processor, 11U, 1U);
  const auto accepted_two = ProcessVisible(processor, 12U, 1U);
  Require(consensus_two.ocr_executed && !consensus_two.accepted &&
              !accepted_two.ocr_executed && accepted_two.accepted &&
              ocr.calls() == 6U,
          "round two must independently OCR once and verify a second frame");

  const auto database_path = runtime->DatabasePath();
  const auto jsonl_path = runtime->JsonlPath();
  const auto output_directory = runtime->OutputDirectory();
  Require(runtime->Close().IsSuccess(), "SessionRuntime must close cleanly");
  ValidatePersistence(database_path, jsonl_path, output_directory);
  std::cout << "FRAME_PROCESSOR_DATABASE=" << PathUtf8(database_path) << '\n';
  std::cout << "FRAME_PROCESSOR_OCR_CALLS=" << ocr.calls() << '\n';
}

void TestSameScreenContentChangeRearmsAfterFailure() {
  ScriptedOcr ocr{
      {BackendUnavailableScan(), ExactSimpleScan(), ExactSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,    SimpleCandidates(), runtime,
                                  "Ahri", std::nullopt,       DetectorConfig()};

  (void)ProcessVisible(processor, 1U, 0U);
  (void)ProcessVisible(processor, 2U, 0U);
  const auto failed_a = ProcessVisible(processor, 3U, 0U);
  const auto changed_once = ProcessVisible(processor, 4U, 1U);
  const auto changed_twice = ProcessVisible(processor, 5U, 1U);
  const auto accepted_b = ProcessVisible(processor, 6U, 1U);
  Require(
      failed_a.reason == "ocr_backend_unavailable" &&
          changed_once.reason == "awaiting_content_change_confirmation:1/2" &&
          changed_twice.reason == "awaiting_ocr_consensus:1/2" &&
          accepted_b.accepted && runtime.calls() == 1U && ocr.calls() == 6U,
      "continuous visible A-to-B must confirm content then safely re-arm");
}

void TestCompletedOfferOffsetPersistsAbsoluteRound() {
  ScriptedOcr ocr{{ExactSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{
      ocr,
      SimpleCandidates(),
      runtime,
      "Ahri",
      std::nullopt,
      DetectorConfig(),
      lol_assistant::detector::StableDetectorConfig{},
      0.85F,
      {},
      std::nullopt,
      2U};

  (void)ProcessVisible(processor, 1U);
  (void)ProcessVisible(processor, 2U);
  (void)ProcessVisible(processor, 3U);
  const auto accepted = ProcessVisible(processor, 4U);
  Require(accepted.accepted && runtime.states.size() == 1U &&
              runtime.states[0].offer_round == 3U,
          "--completed-offers must offset reducer output to absolute stage");
}

void TestBackendFailureRecoversWithBoundedBackoff() {
  ScriptedOcr ocr{
      {BackendUnavailableScan(), ExactSimpleScan(), ExactSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,    SimpleCandidates(), runtime,
                                  "Ahri", std::nullopt,       DetectorConfig()};

  (void)ProcessVisible(processor, 1U);
  (void)ProcessVisible(processor, 2U);
  const auto failed = ProcessVisible(processor, 3U);
  const auto backoff = ProcessVisible(processor, 4U);
  const auto first_good = ProcessVisible(processor, 5U);
  const auto recovered = ProcessVisible(processor, 6U);
  Require(failed.reason == "ocr_backend_unavailable" &&
              backoff.reason == "retry_backoff" &&
              first_good.reason == "awaiting_ocr_consensus:1/2" &&
              recovered.accepted && ocr.calls() == 6U && runtime.calls() == 1U,
          "transient backend failure must back off then recover by consensus");
}

void TestForceRecognitionBypassesStabilityButKeepsConsensusAndDuplicateGate() {
  ScriptedOcr ocr{{ExactSimpleScan(), ExactSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,    SimpleCandidates(), runtime,
                                  "Ahri", std::nullopt,       DetectorConfig()};

  const auto first = ProcessVisibleForced(processor, 1U);
  const auto accepted = ProcessVisibleForced(processor, 2U);
  const std::string first_diagnostic =
      "forced first frame may bypass stability but not OCR consensus: raw=" +
      std::to_string(first.raw_detector.visible) +
      ";stable=" + std::to_string(first.stable_detector.visible) +
      ";force=" + std::to_string(first.force_recognition) +
      ";bypassed=" + std::to_string(first.detector_stability_bypassed) +
      ";ocr=" + std::to_string(first.ocr_executed) +
      ";accepted=" + std::to_string(first.accepted) +
      ";reason=" + first.reason;
  Require(first.raw_detector.visible && !first.stable_detector.visible &&
              first.force_recognition && first.detector_stability_bypassed &&
              first.ocr_executed && !first.accepted &&
              first.reason == "awaiting_ocr_consensus:1/2",
          first_diagnostic);
  Require(accepted.raw_detector.visible && !accepted.stable_detector.visible &&
              accepted.force_recognition &&
              accepted.detector_stability_bypassed && accepted.accepted &&
               accepted.reason == "accepted_offer" && ocr.calls() == 3U &&
              runtime.calls() == 1U,
          "forced second identical OCR frame may persist through normal gates");
  Require(runtime.states.size() == 1U &&
              runtime.states[0].metadata.source ==
                  "augment_frame_processor:force_recognition_game_state" &&
              runtime.states[0].current_offer.has_value() &&
              runtime.states[0].current_offer->metadata.source ==
                  "augment_frame_processor:force_recognition_offer",
          "persisted force-recognition metadata must remain explicit");

  const auto duplicate = ProcessVisibleForced(processor, 3U);
  Require(duplicate.force_recognition && duplicate.duplicate &&
              !duplicate.accepted &&
              duplicate.reason == "same_content_already_processed" &&
               runtime.calls() == 1U && ocr.calls() == 3U,
          "forced duplicate content must never repeat OCR persistence");
}

void TestForceRecognitionKeepsRawDetectorAndUnknownFailClosed() {
  ScriptedOcr ocr{{UnknownScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,    SimpleCandidates(), runtime,
                                  "Ahri", std::nullopt,       DetectorConfig()};

  const auto invisible = ProcessInvisibleForced(processor, 1U);
  Require(invisible.force_recognition && !invisible.raw_detector.visible &&
              !invisible.ocr_executed && runtime.calls() == 0U,
          "force recognition must not fabricate raw detector visibility");

  for (std::uint64_t frame_id = 2U; frame_id <= 6U; ++frame_id) {
    const auto unknown = ProcessVisibleForced(processor, frame_id);
    Require(unknown.force_recognition && unknown.ocr_executed &&
                !unknown.accepted && !unknown.duplicate &&
                unknown.reason == "recognition_unknown",
            "forced OCR UNKNOWN must remain fail closed on every burst frame");
  }
  Require(ocr.calls() == 15U && runtime.calls() == 0U,
          "forced burst may bypass retry exhaustion but never persist UNKNOWN");
  const auto automatic_after_burst = ProcessVisible(processor, 7U);
  Require(!automatic_after_burst.ocr_executed &&
              automatic_after_burst.reason == "content_retry_limit_reached" &&
              ocr.calls() == 15U,
          "default processing must retain the exhausted retry gate");
}

void TestClickRereadReexecutesOcrWithoutRepersisting() {
  ScriptedOcr ocr{{ExactSimpleScan(), ExactSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,    SimpleCandidates(), runtime,
                                  "Ahri", std::nullopt,       DetectorConfig()};

  (void)ProcessVisibleForced(processor, 1U);
  const auto accepted = ProcessVisibleForced(processor, 2U);
  const auto duplicate = ProcessVisibleForced(processor, 3U);
  Require(accepted.accepted && duplicate.duplicate &&
              duplicate.reason == "same_content_already_processed" &&
              runtime.calls() == 1U && ocr.calls() == 3U,
          "accepted content must stay quiet until a click asks for a reread");

  const auto reread = processor.Process(
      MakeFrame(4U, true), FrameProcessOptions{true, true});
  Require(reread.ocr_executed && !reread.accepted && reread.duplicate &&
              runtime.calls() == 1U && ocr.calls() == 6U,
          "a click reread must run OCR again even on the same pixels");

  const auto confirmed = ProcessVisibleForced(processor, 5U);
  Require(!confirmed.ocr_executed && confirmed.duplicate &&
              !confirmed.accepted && runtime.calls() == 1U &&
              ocr.calls() == 6U,
          "reread of the same offer must not persist a second time");
}

void TestClickRereadWithoutOfferDoesNotInventOcr() {
  ScriptedOcr ocr{{ExactSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,    SimpleCandidates(), runtime,
                                  "Ahri", std::nullopt,       DetectorConfig()};
  const auto reread = processor.Process(
      MakeFrame(1U, false), FrameProcessOptions{true, true});
  Require(!reread.ocr_executed && !reread.accepted &&
              reread.reason == "no_offer_on_screen" && ocr.calls() == 0U,
          "a click on a screen without hexcore cards must not invent ROIs "
          "or run OCR");
}

void TestEligibleWindowRereadUsesLowConfidenceRois() {
  ScriptedOcr ocr{{ExactSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,    SimpleCandidates(), runtime,
                                  "Ahri", std::nullopt,       DetectorConfig()};
  const auto reread = processor.Process(
      MakeFrame(1U, false), FrameProcessOptions{true, true, true});
  Require(!reread.raw_detector.visible && reread.ocr_executed &&
              ocr.calls() > 0U,
          "an eligible selection window may OCR calibrated ROIs when the "
          "screen detector misses the offer");
}

void TestForceRecognitionBypassesPersistenceBackoffWithoutSkippingStorage() {
  ScriptedOcr ocr{{ExactSimpleScan(), ExactSimpleScan()}};
  FaultInjectingRuntime runtime{
      {SessionRuntimeError::StorageError, SessionRuntimeError::Ok}};
  AugmentFrameProcessor processor{ocr,    SimpleCandidates(), runtime,
                                  "Ahri", std::nullopt,       DetectorConfig()};

  const auto first = ProcessVisibleForced(processor, 1U);
  const auto failed = ProcessVisibleForced(processor, 2U);
  const auto retried = ProcessVisibleForced(processor, 3U);
  Require(first.reason == "awaiting_ocr_consensus:1/2" &&
              failed.reason == "session_storage_error" && !failed.accepted &&
              retried.accepted && retried.reason == "accepted_offer" &&
              runtime.calls() == 2U && ocr.calls() == 3U,
          "forced burst must bypass backoff but retry the real persistence seam");
}

void RequireFailureStopsAtThreeScans(const OcrScan& scan,
                                     const std::string_view context) {
  ScriptedOcr ocr{{scan}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,    SimpleCandidates(), runtime,
                                  "Ahri", std::nullopt,       DetectorConfig()};

  for (std::uint64_t frame_id = 1U; frame_id <= 10U; ++frame_id) {
    (void)ProcessVisible(processor, frame_id);
  }
  Require(ocr.calls() == 9U && runtime.calls() == 0U,
          std::string{context} + " must stop after three 3-card scans");
  const auto exhausted = ProcessVisible(processor, 11U);
  Require(!exhausted.ocr_executed &&
              exhausted.reason == "content_retry_limit_reached" &&
              ocr.calls() == 9U,
          std::string{context} + " must remain quiet until content changes");
}

void TestRetryableRecognitionFailuresStopAtThreeScans() {
  RequireFailureStopsAtThreeScans(BackendUnavailableScan(),
                                  "backend unavailable");
  RequireFailureStopsAtThreeScans(FailedScan(), "OCR failure");
  RequireFailureStopsAtThreeScans(UnknownScan(), "unknown lexical result");
}

void TestPersistenceRollbackRetryAndSelectedConsumption() {
  ScriptedOcr ocr{{ExactSimpleScan(), ExactSecondRoundScan()}};
  FaultInjectingRuntime runtime{{SessionRuntimeError::StorageError,
                                 SessionRuntimeError::Ok,
                                 SessionRuntimeError::Ok}};
  AugmentFrameProcessor processor{ocr, TwoRoundCandidates(), runtime, "Ahri",
                                  1U,  DetectorConfig()};

  (void)ProcessVisible(processor, 1U, 0U);
  (void)ProcessVisible(processor, 2U, 0U);
  (void)ProcessVisible(processor, 3U, 0U);
  const auto persistence_failed = ProcessVisible(processor, 4U, 0U);
  const auto persistence_backoff = ProcessVisible(processor, 5U, 0U);
  const auto retried = ProcessVisible(processor, 6U, 0U);
  Require(!persistence_failed.accepted &&
              persistence_failed.reason == "session_storage_error" &&
              persistence_backoff.reason == "retry_backoff" &&
              retried.accepted && runtime.calls() == 2U && ocr.calls() == 3U,
          "persistence fault must retry prepared consensus without new OCR");
  Require(
      runtime.states[0].offer_round == 1U &&
          runtime.states[1].offer_round == 1U &&
          runtime.attempted_selections[0] == "beta" &&
          runtime.attempted_selections[1] == "beta",
      "failed persistence must not advance round/revision or consume event");

  (void)ProcessInvisible(processor, 7U);
  (void)ProcessInvisible(processor, 8U);
  (void)ProcessVisible(processor, 9U, 1U);
  (void)ProcessVisible(processor, 10U, 1U);
  (void)ProcessVisible(processor, 11U, 1U);
  const auto round_two = ProcessVisible(processor, 12U, 1U);
  Require(round_two.accepted && runtime.calls() == 3U &&
              runtime.states[2].offer_round == 2U &&
              !runtime.attempted_selections[2].has_value(),
          "successful first commit must consume selected before round two");
  Require(runtime.persisted_selections.size() == 2U &&
              runtime.persisted_selections[0] == "beta" &&
              !runtime.persisted_selections[1].has_value(),
          "only one successful offer may persist the selected event");
}

void TestFuzzyAloneCannotAcceptButNormalizedCan() {
  ScriptedOcr ocr{
      {FuzzySimpleScan(), FuzzySimpleScan(), NormalizedSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,    SimpleCandidates(), runtime,
                                  "Ahri", std::nullopt,       DetectorConfig()};

  (void)ProcessVisible(processor, 1U);
  (void)ProcessVisible(processor, 2U);
  (void)ProcessVisible(processor, 3U);
  const auto fuzzy_consensus = ProcessVisible(processor, 4U);
  const auto backoff = ProcessVisible(processor, 5U);
  const auto normalized = ProcessVisible(processor, 6U);
  Require(!fuzzy_consensus.accepted &&
              fuzzy_consensus.reason == "fuzzy_requires_strong_consensus" &&
              runtime.calls() == 1U && backoff.reason == "retry_backoff" &&
              normalized.accepted && ocr.calls() == 9U,
          "fuzzy-only IDs must not auto-accept; normalized evidence may close "
          "an already stable cross-frame consensus");
}

[[nodiscard]] lol_assistant::detector::AugmentScreenDetectorConfig
FusionDetectorConfig() {
  auto config = DetectorConfig();
  config.roi_search.enabled = false;
  return config;
}

using FusionIconHashes =
    std::array<std::uint64_t, lol_assistant::common::kAugmentCardCount>;

[[nodiscard]] FusionIconHashes ComputeFusionIconHashes(
    const lol_assistant::detector::AugmentScreenDetectorConfig& config) {
  using namespace lol_assistant;
  const auto source_frame = MakeFrame(1U, true, 0U, true);
  const detector::AugmentScreenDetector detector_probe{config};
  const auto detected = detector_probe.Detect(source_frame);
  Require(detected.visible && detected.rois.has_value(),
          "fusion liveness tests require deterministic detector ROIs");

  FusionIconHashes hashes{};
  for (std::size_t slot = 0U; slot < hashes.size(); ++slot) {
    const auto crop = detector::CropRawBgraOwning(
        source_frame, detected.rois->icon_rects[slot]);
    Require(crop.ok(), "fusion liveness test icon crop must be valid");
    const auto hash = vision::ComputeDifferenceHash(*crop.value);
    Require(hash.ok(), "fusion liveness test icon dHash must be valid");
    hashes[slot] = *hash.hash;
  }
  for (std::size_t left = 0U; left < hashes.size(); ++left) {
    for (std::size_t right = left + 1U; right < hashes.size(); ++right) {
      Require(std::popcount(hashes[left] ^ hashes[right]) >= 6,
              "fusion liveness icon hashes must have a unique match margin");
    }
  }
  return hashes;
}

[[nodiscard]] std::vector<lol_assistant::vision::IconHashTemplate>
AgreeingIconTemplates(const FusionIconHashes& hashes) {
  constexpr std::array<std::string_view,
                       lol_assistant::common::kAugmentCardCount>
      ids{"alpha", "beta", "gamma"};
  std::vector<lol_assistant::vision::IconHashTemplate> templates;
  for (std::size_t slot = 0U; slot < hashes.size(); ++slot) {
    templates.push_back({std::string{ids[slot]}, hashes[slot], {"KIWI"}});
  }
  return templates;
}

[[nodiscard]] std::vector<lol_assistant::vision::IconHashTemplate>
AmbiguousIconTemplates(const FusionIconHashes& hashes) {
  auto templates = AgreeingIconTemplates(hashes);
  for (std::size_t slot = 0U; slot < hashes.size(); ++slot) {
    templates.push_back(
        {"ambiguous-tie-" + std::to_string(slot), hashes[slot], {"KIWI"}});
  }
  return templates;
}

[[nodiscard]] std::vector<lol_assistant::vision::IconHashTemplate>
UnknownIconTemplates(const FusionIconHashes& hashes) {
  std::uint64_t candidate = 0x9E3779B97F4A7C15ULL;
  for (std::uint32_t attempt = 0U; attempt < 64U; ++attempt) {
    const bool safely_distant =
        std::all_of(hashes.begin(), hashes.end(), [candidate](const auto hash) {
          return std::popcount(candidate ^ hash) > 10;
        });
    if (safely_distant) {
      return {{"distant-icon", candidate, {"KIWI"}}};
    }
    candidate = candidate * 6364136223846793005ULL + 1442695040888963407ULL;
  }
  throw std::runtime_error(
      "failed to construct a deterministic unknown icon template");
}

[[nodiscard]] std::vector<lol_assistant::vision::IconHashTemplate>
ConflictingIconTemplates(const FusionIconHashes& hashes) {
  constexpr std::array<std::string_view,
                       lol_assistant::common::kAugmentCardCount>
      conflicting_ids{"beta", "gamma", "alpha"};
  std::vector<lol_assistant::vision::IconHashTemplate> templates;
  for (std::size_t slot = 0U; slot < hashes.size(); ++slot) {
    templates.push_back(
        {std::string{conflicting_ids[slot]}, hashes[slot], {"KIWI"}});
  }
  return templates;
}

void PrimeFusionProcessor(AugmentFrameProcessor& processor) {
  (void)ProcessVisible(processor, 1U, 0U, true);
  (void)ProcessVisible(processor, 2U, 0U, true);
}

void RequireOcrOnlyCard(
    const lol_assistant::vision::CardRecognitionOutput& card,
    const std::string_view lexical_reason,
    const lol_assistant::vision::IconMatchState expected_icon_state,
    const std::string_view icon_reason, const float expected_confidence) {
  const std::string expected_reason =
      std::string{lexical_reason} +
      "+ocr_only_icon_unknown:" + std::string{icon_reason};
  Require(
      card.state == lol_assistant::vision::CardRecognitionState::Recognized &&
          card.augment_id.has_value() &&
          card.icon_match.state == expected_icon_state &&
          card.icon_match.reason == icon_reason &&
          card.reason == expected_reason,
      "OCR-only fusion must preserve identity and expose icon diagnostics");
  Require(std::abs(card.final_confidence - expected_confidence) < 0.0001F,
          "OCR-only fusion must apply its explicit confidence downgrade");
}

void TestExactOcrWithAmbiguousIconPersistsAfterTwoFrames() {
  using namespace lol_assistant;
  const auto config = FusionDetectorConfig();
  const auto hashes = ComputeFusionIconHashes(config);
  auto scan = ExactSimpleScan();
  scan.confidence.reset();
  ScriptedOcr ocr{{scan, scan}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,
                                  SimpleCandidates(),
                                  runtime,
                                  "Ahri",
                                  std::nullopt,
                                  config,
                                  detector::StableDetectorConfig{},
                                  0.85F,
                                  AmbiguousIconTemplates(hashes),
                                  "KIWI"};

  PrimeFusionProcessor(processor);
  const auto first = ProcessVisible(processor, 3U, 0U, true);
  const auto second = ProcessVisible(processor, 4U, 0U, true);
  Require(first.reason == "awaiting_ocr_consensus:1/2" && !first.accepted &&
               second.accepted && second.reason == "accepted_offer" &&
               runtime.calls() == 1U && ocr.calls() == 3U,
          "exact OCR plus ambiguous icon must reuse OCR on frame two");
  Require(first.recognition.has_value() && runtime.attempted_cards.size() == 1U,
          "exact/ambiguous persistence must retain fused diagnostics");
  for (const auto& card : first.recognition->cards) {
    RequireOcrOnlyCard(card, "exact_match", vision::IconMatchState::Unknown,
                       "hash_top1_ambiguous", 0.85F);
  }
  for (const auto& card : runtime.attempted_cards[0]) {
    RequireOcrOnlyCard(card, "exact_match", vision::IconMatchState::Unknown,
                       "hash_top1_ambiguous", 0.85F);
  }
}

void TestNormalizedOcrWithUnknownIconPersistsAfterTwoFrames() {
  using namespace lol_assistant;
  const auto config = FusionDetectorConfig();
  const auto hashes = ComputeFusionIconHashes(config);
  auto scan = NormalizedSimpleScan();
  scan.confidence.reset();
  ScriptedOcr ocr{{scan, scan}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,
                                  SimpleCandidates(),
                                  runtime,
                                  "Ahri",
                                  std::nullopt,
                                  config,
                                  detector::StableDetectorConfig{},
                                  0.85F,
                                  UnknownIconTemplates(hashes),
                                  "KIWI"};

  PrimeFusionProcessor(processor);
  const auto first = ProcessVisible(processor, 3U, 0U, true);
  const auto second = ProcessVisible(processor, 4U, 0U, true);
  Require(first.reason == "awaiting_ocr_consensus:1/2" && !first.accepted &&
               second.accepted && second.reason == "accepted_offer" &&
               runtime.calls() == 1U && ocr.calls() == 3U,
          "normalized OCR plus unknown icon must reuse OCR on frame two");
  Require(first.recognition.has_value() && runtime.attempted_cards.size() == 1U,
          "normalized/unknown persistence must retain fused diagnostics");
  for (const auto& card : first.recognition->cards) {
    RequireOcrOnlyCard(card, "normalized_match",
                       vision::IconMatchState::Unknown,
                       "hash_distance_above_threshold", 0.85F);
  }
  for (const auto& card : runtime.attempted_cards[0]) {
    RequireOcrOnlyCard(card, "normalized_match",
                       vision::IconMatchState::Unknown,
                       "hash_distance_above_threshold", 0.85F);
  }
}

void TestFuzzyOcrWithUnknownIconDoesNotPersist() {
  using namespace lol_assistant;
  const auto config = FusionDetectorConfig();
  const auto hashes = ComputeFusionIconHashes(config);
  ScriptedOcr ocr{{FuzzySimpleScan(), FuzzySimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,
                                  SimpleCandidates(),
                                  runtime,
                                  "Ahri",
                                  std::nullopt,
                                  config,
                                  detector::StableDetectorConfig{},
                                  0.85F,
                                  UnknownIconTemplates(hashes),
                                  "KIWI"};

  PrimeFusionProcessor(processor);
  const auto first = ProcessVisible(processor, 3U, 0U, true);
  const auto second = ProcessVisible(processor, 4U, 0U, true);
  Require(first.reason == "awaiting_ocr_consensus:1/2" && !first.accepted &&
              second.reason == "fuzzy_requires_strong_consensus" &&
              !second.accepted && runtime.calls() == 0U && ocr.calls() == 6U,
          "fuzzy OCR plus unknown icon must remain blocked after two frames");
  Require(second.recognition.has_value(),
          "blocked fuzzy/unknown fusion must expose diagnostics");
  for (const auto& card : second.recognition->cards) {
    Require(card.state == vision::CardRecognitionState::Recognized &&
                card.icon_match.state == vision::IconMatchState::Unknown &&
                card.reason ==
                    "fuzzy_match+ocr_only_icon_unknown:"
                    "hash_distance_above_threshold" &&
                card.final_confidence < 0.85F,
            "fuzzy/unknown evidence must be downgraded and never be strong");
  }
}

void TestExactOcrWithAgreeingIconPersistsWithoutDowngrade() {
  using namespace lol_assistant;
  const auto config = FusionDetectorConfig();
  const auto hashes = ComputeFusionIconHashes(config);
  ScriptedOcr ocr{{ExactSimpleScan(), ExactSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,
                                  SimpleCandidates(),
                                  runtime,
                                  "Ahri",
                                  std::nullopt,
                                  config,
                                  detector::StableDetectorConfig{},
                                  0.85F,
                                  AgreeingIconTemplates(hashes),
                                  "KIWI"};

  PrimeFusionProcessor(processor);
  const auto first = ProcessVisible(processor, 3U, 0U, true);
  const auto second = ProcessVisible(processor, 4U, 0U, true);
  Require(first.reason == "awaiting_ocr_consensus:1/2" && !first.accepted &&
               second.accepted && second.reason == "accepted_offer" &&
               runtime.calls() == 1U && ocr.calls() == 3U,
          "exact OCR plus agreeing icon must reuse OCR on frame two");
  Require(runtime.attempted_cards.size() == 1U,
          "exact/agree persistence must expose persisted cards");
  for (const auto& card : runtime.attempted_cards[0]) {
    Require(card.state == vision::CardRecognitionState::Recognized &&
                card.icon_match.state == vision::IconMatchState::Matched &&
                card.reason == "exact_match+ocr_icon_agree" &&
                std::abs(card.final_confidence - 0.98F) < 0.0001F,
            "agreeing icon must preserve full OCR confidence and reason");
  }
}

void TestExactOcrWithConflictingIconFailsClosedAcrossFrames() {
  using namespace lol_assistant;
  const auto config = FusionDetectorConfig();
  const auto hashes = ComputeFusionIconHashes(config);
  ScriptedOcr ocr{{ExactSimpleScan(), ExactSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,
                                  SimpleCandidates(),
                                  runtime,
                                  "Ahri",
                                  std::nullopt,
                                  config,
                                  detector::StableDetectorConfig{},
                                  0.85F,
                                  ConflictingIconTemplates(hashes),
                                  "KIWI"};

  PrimeFusionProcessor(processor);
  const auto first = ProcessVisible(processor, 3U, 0U, true);
  const auto backoff = ProcessVisible(processor, 4U, 0U, true);
  const auto second = ProcessVisible(processor, 5U, 0U, true);
  Require(first.reason == "recognition_unknown" &&
              backoff.reason == "retry_backoff" &&
              second.reason == "recognition_unknown" && !first.accepted &&
              !second.accepted && runtime.calls() == 0U && ocr.calls() == 6U,
          "exact OCR plus conflicting icon must fail closed across two scans");
  Require(first.recognition.has_value() && second.recognition.has_value(),
          "conflict rejection must retain both frame diagnostics");
  for (const auto* recognition : {&*first.recognition, &*second.recognition}) {
    for (const auto& card : recognition->cards) {
      Require(card.state == vision::CardRecognitionState::Unknown &&
                  !card.augment_id.has_value() &&
                  card.final_confidence == 0.0F &&
                  card.icon_match.state == vision::IconMatchState::Matched &&
                  card.reason.starts_with("ocr_icon_conflict:"),
              "explicit unique icon conflict must clear OCR identity");
    }
  }
}

void TestModeAwareIconConflictFailsClosed() {
  using namespace lol_assistant;
  auto detector_config = DetectorConfig();
  detector_config.roi_search.enabled = false;
  const auto source_frame = MakeFrame(1U, true);
  const detector::AugmentScreenDetector detector_probe{detector_config};
  const auto detected = detector_probe.Detect(source_frame);
  Require(detected.visible && detected.rois.has_value(),
          "icon fusion test requires deterministic detector ROIs");
  std::vector<vision::IconHashTemplate> templates;
  for (const auto& icon_roi : detected.rois->icon_rects) {
    const auto icon_crop = detector::CropRawBgraOwning(source_frame, icon_roi);
    Require(icon_crop.ok(), "icon fusion test requires a valid icon crop");
    const auto hash = vision::ComputeDifferenceHash(*icon_crop.value);
    Require(hash.ok(), "icon fusion test requires a deterministic dHash");
    templates.push_back({"icon-conflict", *hash.hash, {"KIWI"}});
    templates.push_back({"excluded-mode-tie", *hash.hash, {"KIWI_JADE"}});
    templates.push_back({"mode-aware-top2", *hash.hash ^ 0xFFFFU, {"KIWI"}});
  }
  ScriptedOcr ocr{{ExactSimpleScan()}};
  FaultInjectingRuntime runtime;
  AugmentFrameProcessor processor{ocr,
                                  SimpleCandidates(),
                                  runtime,
                                  "Ahri",
                                  std::nullopt,
                                  detector_config,
                                  detector::StableDetectorConfig{},
                                  0.85F,
                                  std::move(templates),
                                  "KIWI"};

  (void)ProcessVisible(processor, 1U);
  (void)ProcessVisible(processor, 2U);
  const auto conflict = ProcessVisible(processor, 3U);
  Require(conflict.recognition.has_value() &&
              conflict.reason == "recognition_unknown" && runtime.calls() == 0U,
          "OCR/icon conflict must fail closed before persistence");
  for (const auto& card : conflict.recognition->cards) {
    Require(
        card.icon_match.state == vision::IconMatchState::Matched,
        std::string{"mode-aware icon template must produce a bounded match: "} +
            card.icon_match.reason);
    Require(card.reason.starts_with("ocr_icon_conflict:"),
            "OCR/icon disagreement must report an explicit conflict");
    Require(card.state == vision::CardRecognitionState::Unknown &&
                !card.augment_id.has_value(),
            "mode-aware icon conflict must clear the final augment identity");
    Require(card.icon_match.top1_score.has_value() &&
                card.icon_match.top2_score.has_value() &&
                card.icon_match.margin.has_value() &&
                card.icon_match.candidate_ids.size() == 2U &&
                card.icon_match.candidate_ids[0] == "icon-conflict",
            "icon diagnostics must retain deterministic top1/top2/margin");
  }
}

}  // namespace

int main() {
  try {
    winrt::init_apartment(winrt::apartment_type::single_threaded);
    TestTwoFrameConsensusRoundAdvanceAndSqlite();
    TestSameScreenContentChangeRearmsAfterFailure();
    TestCompletedOfferOffsetPersistsAbsoluteRound();
    TestBackendFailureRecoversWithBoundedBackoff();
    TestForceRecognitionBypassesStabilityButKeepsConsensusAndDuplicateGate();
    TestClickRereadReexecutesOcrWithoutRepersisting();
    TestClickRereadWithoutOfferDoesNotInventOcr();
    TestEligibleWindowRereadUsesLowConfidenceRois();
    TestForceRecognitionKeepsRawDetectorAndUnknownFailClosed();
    TestForceRecognitionBypassesPersistenceBackoffWithoutSkippingStorage();
    TestRetryableRecognitionFailuresStopAtThreeScans();
    TestPersistenceRollbackRetryAndSelectedConsumption();
    TestFuzzyAloneCannotAcceptButNormalizedCan();
    TestExactOcrWithAmbiguousIconPersistsAfterTwoFrames();
    TestNormalizedOcrWithUnknownIconPersistsAfterTwoFrames();
    TestFuzzyOcrWithUnknownIconDoesNotPersist();
    TestExactOcrWithAgreeingIconPersistsWithoutDowngrade();
    TestExactOcrWithConflictingIconFailsClosedAcrossFrames();
    TestModeAwareIconConflictFailsClosed();
    std::cout << "[PASS] augment frame processor integration\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "[FAIL] augment frame processor integration: " << error.what()
              << '\n';
    return 1;
  }
}
