#include "session_runtime.h"

#include <Windows.h>
#include <winsqlite/winsqlite3.h>

#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Data.Json.h>
#include <winrt/base.h>

#include <algorithm>
#include <array>
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
#include <vector>

#include "lol_assistant/knowledge/augment_catalog.h"
#include "lol_assistant/replay/wic_image_codec.h"

#ifndef LOL_SESSION_ADAPTER_TMP_ROOT
#error LOL_SESSION_ADAPTER_TMP_ROOT must point to outputs/tmp/session_adapter
#endif

#ifndef LOL_SESSION_RUNTIME_ROOT
#error LOL_SESSION_RUNTIME_ROOT must point to outputs/runtime
#endif

#ifndef LOL_ASSISTANT_CATALOG_PATH
#error LOL_ASSISTANT_CATALOG_PATH must point to the real augment catalog
#endif

namespace {

using lol_assistant::app::AcceptedOfferResult;
using lol_assistant::app::Phase1SessionRuntime;
using lol_assistant::app::SessionRuntimeError;
using lol_assistant::app::SessionRuntimeStatus;

void Require(const bool condition, const std::string_view message) {
  if (!condition) {
    throw std::runtime_error(std::string{message});
  }
}

void RequireStatus(const SessionRuntimeStatus& status,
                   const std::string_view context) {
  if (!status.IsSuccess()) {
    throw std::runtime_error(std::string{context} + ": " + status.message);
  }
}

[[nodiscard]] std::string PathUtf8(const std::filesystem::path& path) {
  const auto bytes = path.generic_u8string();
  return {bytes.begin(), bytes.end()};
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

class RawDatabase final {
 public:
  explicit RawDatabase(const std::filesystem::path& path) {
    const std::string path_utf8 = PathUtf8(path);
    const int result = sqlite3_open_v2(
        path_utf8.c_str(), &database_,
        SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX, nullptr);
    if (result != SQLITE_OK) {
      const std::string message = database_ == nullptr
                                      ? "unknown sqlite error"
                                      : sqlite3_errmsg(database_);
      throw std::runtime_error("SQLite open failed: " + message);
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
    int result =
        sqlite3_prepare_v2(database_, query.c_str(), -1, &statement, nullptr);
    if (result != SQLITE_OK) {
      throw std::runtime_error("SQLite integer prepare failed");
    }
    result = sqlite3_step(statement);
    if (result != SQLITE_ROW) {
      (void)sqlite3_finalize(statement);
      throw std::runtime_error("SQLite integer query returned no row");
    }
    const auto value = sqlite3_column_int64(statement, 0);
    (void)sqlite3_finalize(statement);
    return value;
  }

  [[nodiscard]] std::string ScalarText(const std::string_view sql) const {
    sqlite3_stmt* statement = nullptr;
    const std::string query{sql};
    int result =
        sqlite3_prepare_v2(database_, query.c_str(), -1, &statement, nullptr);
    if (result != SQLITE_OK) {
      throw std::runtime_error("SQLite text prepare failed");
    }
    result = sqlite3_step(statement);
    if (result != SQLITE_ROW) {
      (void)sqlite3_finalize(statement);
      throw std::runtime_error("SQLite text query returned no row");
    }
    const auto* value = sqlite3_column_text(statement, 0);
    const std::string text =
        value == nullptr ? "" : reinterpret_cast<const char*>(value);
    (void)sqlite3_finalize(statement);
    return text;
  }

 private:
  sqlite3* database_{nullptr};
};

[[nodiscard]] std::string ReadFile(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    throw std::runtime_error("unable to read file: " + path.string());
  }
  return {std::istreambuf_iterator<char>{input},
          std::istreambuf_iterator<char>{}};
}

[[nodiscard]] winrt::hstring StrictUtf8ToHString(
    const std::string_view utf8) {
  Require(utf8.size() <= static_cast<std::size_t>(INT_MAX),
          "UTF-8 test input is too large");
  const int required = MultiByteToWideChar(
      CP_UTF8, MB_ERR_INVALID_CHARS, utf8.data(), static_cast<int>(utf8.size()),
      nullptr, 0);
  Require(required > 0, "text is not strict UTF-8");
  std::wstring wide(static_cast<std::size_t>(required), L'\0');
  const int converted = MultiByteToWideChar(
      CP_UTF8, MB_ERR_INVALID_CHARS, utf8.data(), static_cast<int>(utf8.size()),
      wide.data(), required);
  Require(converted == required, "strict UTF-8 conversion was incomplete");
  return winrt::hstring{wide};
}

void RequireJsonObject(const std::string_view utf8,
                       const std::string_view context) {
  try {
    const auto object = winrt::Windows::Data::Json::JsonObject::Parse(
        StrictUtf8ToHString(utf8));
    Require(object.Size() > 0U, context);
  } catch (const winrt::hresult_error& error) {
    throw std::runtime_error(std::string{context} + ": JSON parse failed: " +
                             winrt::to_string(error.message()));
  }
}

[[nodiscard]] lol_assistant::common::Frame MakeFrame(
    const lol_assistant::common::UtcTimestamp observed_at) {
  using namespace lol_assistant::common;
  Frame frame;
  frame.source = {FrameSourceKind::Replay, "session-runtime-integration"};
  frame.frame_id = 9001U;
  frame.timestamps.captured_at_utc = observed_at;
  frame.width = 160U;
  frame.height = 90U;
  frame.stride = frame.width * static_cast<std::uint32_t>(Frame::kBytesPerPixel);
  frame.buffer.resize(static_cast<std::size_t>(frame.stride) * frame.height);
  for (std::uint32_t y = 0U; y < frame.height; ++y) {
    for (std::uint32_t x = 0U; x < frame.width; ++x) {
      const std::size_t offset = static_cast<std::size_t>(y) * frame.stride +
                                 static_cast<std::size_t>(x) * 4U;
      frame.buffer[offset] = static_cast<std::uint8_t>(x);
      frame.buffer[offset + 1U] = static_cast<std::uint8_t>(y * 2U);
      frame.buffer[offset + 2U] = static_cast<std::uint8_t>(255U - x);
      frame.buffer[offset + 3U] = 255U;
    }
  }
  Require(frame.IsValid(), "test frame must be valid");
  return frame;
}

[[nodiscard]] lol_assistant::detector::ThreeCardRois MakeRois() {
  using lol_assistant::detector::PixelRoi;
  return {PixelRoi{8U, 8U, 144U, 74U},
          {PixelRoi{16U, 20U, 32U, 50U},
           PixelRoi{64U, 20U, 32U, 50U},
           PixelRoi{112U, 20U, 32U, 50U}}};
}

[[nodiscard]] lol_assistant::common::NormalizedRoi Normalize(
    const lol_assistant::detector::PixelRoi& roi) {
  return {static_cast<double>(roi.x) / 160.0,
          static_cast<double>(roi.y) / 90.0,
          static_cast<double>(roi.width) / 160.0,
          static_cast<double>(roi.height) / 90.0};
}

struct StableOfferFixture final {
  lol_assistant::common::GameState state{};
  std::array<lol_assistant::vision::CardRecognitionOutput,
             lol_assistant::common::kAugmentCardCount>
      card_outputs{};
  lol_assistant::common::Frame frame{};
  lol_assistant::detector::ThreeCardRois rois{};
  std::array<std::string, lol_assistant::common::kAugmentCardCount> ids{};
};

[[nodiscard]] StableOfferFixture MakeStableOffer(
    const lol_assistant::knowledge::AugmentCatalog& catalog) {
  using namespace lol_assistant;
  StableOfferFixture fixture;
  fixture.ids = {"ARAM_ADAPt", "ARAM_AllForYou", "ARAM_ApexInventor"};
  std::array<const knowledge::AugmentRecord*, common::kAugmentCardCount>
      records{};
  for (std::size_t index = 0U; index < records.size(); ++index) {
    records[index] = catalog.FindById(fixture.ids[index]);
    Require(records[index] != nullptr,
            "integration fixture ID must exist in the real catalog");
  }

  const auto observed_at = common::UtcTimestamp::clock::now();
  fixture.frame = MakeFrame(observed_at);
  fixture.rois = MakeRois();

  common::AugmentScreenDetection detection;
  detection.state = common::DetectionState::Detected;
  detection.offer_roi = Normalize(fixture.rois.offer_region);
  constexpr std::array<common::CardSlotId, common::kAugmentCardCount> slots{
      common::CardSlotId::Left, common::CardSlotId::Center,
      common::CardSlotId::Right};
  for (std::size_t index = 0U; index < slots.size(); ++index) {
    detection.card_slots[index] =
        common::CardSlot{slots[index], Normalize(fixture.rois.cards[index])};
  }
  detection.metadata.source = "integration-stable-detector";
  detection.metadata.confidence = common::Confidence{0.99F};
  detection.metadata.observed_at = observed_at;

  common::AugmentOfferObservation offer;
  offer.screen_detection = detection;
  offer.metadata.source = "integration-stable-offer";
  offer.metadata.confidence = common::Confidence{0.94F};
  offer.metadata.observed_at = observed_at;
  for (std::size_t index = 0U; index < records.size(); ++index) {
    common::AugmentRecognition recognition;
    recognition.state = common::RecognitionState::Recognized;
    recognition.slot = slots[index];
    recognition.augment_id = records[index]->id;
    if (index != 1U) {
      recognition.display_name = records[index]->display_name;
    }
    recognition.metadata.source = "integration-stable-recognizer";
    recognition.metadata.confidence = common::Confidence{
        0.96F - static_cast<float>(index) * 0.01F};
    recognition.metadata.observed_at = observed_at;
    offer.recognitions[index] = recognition;

    auto& card = fixture.card_outputs[index];
    card.state = vision::CardRecognitionState::Recognized;
    card.raw_text = records[index]->display_name;
    card.backend = "caller-provided-stable-result";
    if (index != 1U) {
      card.ocr_confidence = 0.97F - static_cast<float>(index) * 0.01F;
      card.display_name = records[index]->display_name;
    }
    card.match_confidence = 0.98F - static_cast<float>(index) * 0.01F;
    card.final_confidence = 0.96F - static_cast<float>(index) * 0.01F;
    card.augment_id = records[index]->id;
    card.reason = "stable_input_accepted_without_running_ocr";
    card.icon_match.state = vision::IconMatchState::Unknown;
    card.icon_match.reason = "not_supplied";
  }

  fixture.state.champion = "阿狸";
  fixture.state.offer_round = 2U;
  fixture.state.current_offer = offer;
  fixture.state.metadata.source = "integration-game-state";
  fixture.state.metadata.confidence = common::Confidence{0.94F};
  fixture.state.metadata.observed_at = observed_at;
  Require(fixture.state.IsValid(), "stable test GameState must be valid");
  return fixture;
}

void TestPathRejection(const std::filesystem::path& runtime_root) {
  std::unique_ptr<Phase1SessionRuntime> runtime;
  const auto relative = Phase1SessionRuntime::Create(
      L"outputs/runtime", "阿狸", "KIWI",
      {"integration", "stable", "catalog", std::nullopt}, runtime);
  Require(relative.code == SessionRuntimeError::InvalidPath && runtime == nullptr,
          "relative runtime_root must be rejected");

  const auto lexical_escape = Phase1SessionRuntime::Create(
      runtime_root / L"nested" / L".." / L"escape", "阿狸", "KIWI",
      {"integration", "stable", "catalog", std::nullopt}, runtime);
  Require(lexical_escape.code == SessionRuntimeError::InvalidPath &&
              runtime == nullptr,
          "runtime_root containing '..' must be rejected before filesystem IO");
}

void ValidateJsonl(const std::filesystem::path& jsonl_path,
                   const std::string& expected_chinese) {
  const std::string contents = ReadFile(jsonl_path);
  Require(!contents.empty(), "JSONL must not be empty");
  Require(contents.size() < 3U ||
              contents.substr(0U, 3U) != std::string{"\xEF\xBB\xBF", 3U},
          "JSONL must not contain a UTF-8 BOM");
  Require(contents.find(expected_chinese) != std::string::npos,
          "JSONL must preserve Chinese UTF-8 text");

  std::size_t start = 0U;
  std::size_t line_count = 0U;
  std::size_t accepted_count = 0U;
  const std::array<std::string_view, 4U> expected_types{
      "\"event_type\":\"start\"", "\"event_type\":\"diagnostic\"",
      "\"event_type\":\"accepted_offer\"", "\"event_type\":\"end\""};
  while (start < contents.size()) {
    const std::size_t end = contents.find('\n', start);
    Require(end != std::string::npos, "every JSONL event must end with LF");
    const std::string_view line{contents.data() + start, end - start};
    Require(!line.empty(), "JSONL must not contain blank lines");
    RequireJsonObject(line, "JSONL line must be a strict UTF-8 JSON object");
    Require(line_count < expected_types.size() &&
                line.find(expected_types[line_count]) != std::string_view::npos,
            "JSONL event ordering must be start/diagnostic/accepted_offer/end");
    if (line.find("\"event_type\":\"accepted_offer\"") !=
        std::string_view::npos) {
      ++accepted_count;
    }
    ++line_count;
    start = end + 1U;
  }
  Require(line_count == 4U, "JSONL must contain exactly four lifecycle events");
  Require(accepted_count == 1U,
          "duplicate offer must not append another accepted_offer event");
}

void ValidatePngs(const AcceptedOfferResult& accepted,
                  const lol_assistant::common::Frame& source) {
  using namespace lol_assistant;
  Require(accepted.artifacts.has_value(), "accepted offer must return artifacts");
  const auto& artifacts = *accepted.artifacts;
  const auto raw = replay::WicImageCodec::Decode(
      artifacts.raw_frame, source.source, source.frame_id);
  Require(raw.width == 160U && raw.height == 90U,
          "RAW PNG must WIC-decode at 160x90");
  constexpr std::array<std::string_view, common::kAugmentCardCount> labels{
      "_LEFT.png", "_CENTER.png", "_RIGHT.png"};
  for (std::size_t index = 0U; index < artifacts.card_rois.size(); ++index) {
    const auto crop = replay::WicImageCodec::Decode(
        artifacts.card_rois[index], source.source, source.frame_id);
    Require(crop.width == 32U && crop.height == 50U,
            "card PNG must WIC-decode at the exact 32x50 ROI size");
    Require(artifacts.card_rois[index].filename().string().find(labels[index]) !=
                std::string::npos,
            "card PNG filename must retain semantic slot label");
  }
  Require(artifacts.raw_frame.filename().string().find("_RAW.png") !=
              std::string::npos,
          "RAW PNG filename must retain RAW label");
  RequireJsonObject(ReadFile(artifacts.sidecar_json),
                    "artifact sidecar must be strict UTF-8 JSON");
}

void ValidateDatabase(const std::filesystem::path& database_path) {
  RawDatabase database{database_path};
  Require(database.ScalarText("PRAGMA integrity_check;") == "ok",
          "SQLite integrity_check must be ok");
  Require(database.ScalarInt("SELECT COUNT(*) FROM pragma_foreign_key_check;") ==
              0,
          "SQLite foreign_key_check must return zero rows");
  Require(database.ScalarInt("SELECT COUNT(*) FROM sessions;") == 1,
          "sessions must contain one row");
  Require(database.ScalarInt("SELECT COUNT(*) FROM augment_offers;") == 1,
          "duplicate offer must leave one augment_offers row");
  Require(database.ScalarInt("SELECT COUNT(*) FROM recognition_results;") == 3,
          "accepted offer must contain three recognition rows");
  Require(database.ScalarInt("SELECT COUNT(*) FROM augment_choices;") == 1,
          "selected offer must contain one choice row");
  Require(database.ScalarInt("SELECT COUNT(*) FROM artifacts;") == 5,
          "RAW/LEFT/CENTER/RIGHT/sidecar must contain five artifact rows");
  Require(database.ScalarInt(
              "SELECT COUNT(*) FROM recognition_results WHERE offer_id=1;") ==
              3,
          "all recognition rows must reference the accepted offer");
  Require(database.ScalarInt(
              "SELECT COUNT(*) FROM sessions WHERE ended_at_utc IS NOT NULL;") ==
              1,
          "closed session must persist ended_at_utc");
  Require(database.ScalarText(
              "SELECT raw_json FROM recognition_results WHERE slot=2;")
              .find("\"ocr_confidence\":null") != std::string::npos,
          "raw recognition JSON must preserve nullable OCR confidence");
  Require(database.ScalarText(
              "SELECT raw_json FROM recognition_results WHERE slot=2;")
              .find("\"state\":\"UNKNOWN\"") != std::string::npos,
          "raw recognition JSON must preserve UNKNOWN icon state");
}

void TestSessionLifecycle(const std::filesystem::path& runtime_root,
                          const lol_assistant::knowledge::AugmentCatalog& catalog) {
  using namespace lol_assistant;
  const StableOfferFixture fixture = MakeStableOffer(catalog);
  std::unique_ptr<Phase1SessionRuntime> runtime;
  RequireStatus(Phase1SessionRuntime::Create(
                    runtime_root, "阿狸", "KIWI",
                    {"integration-caller", "caller-provided-stable-result",
                     catalog.catalog_version(), "replay-fixture"},
                    runtime),
                "create Phase1SessionRuntime");
  Require(runtime != nullptr && runtime->IsOpen(),
          "created session runtime must be open");
  Require(runtime->SessionId().starts_with("phase1-") &&
              runtime->SessionId().size() == 39U &&
              runtime->SessionId().find_first_not_of(
                  "0123456789abcdef", 7U) == std::string::npos,
          "session_id must be a safe phase1- plus 128-bit lowercase hex ID");
  Require(IsStrictDescendant(runtime_root, runtime->OutputDirectory()),
          "session output must be a strict descendant of runtime_root");

  common::Frame bad_frame = fixture.frame;
  bad_frame.buffer.pop_back();
  const auto bad_frame_result = runtime->AcceptOffer(
      fixture.state, fixture.card_outputs, bad_frame, fixture.rois,
      fixture.ids[1U]);
  Require(bad_frame_result.status.code == SessionRuntimeError::InvalidArgument &&
              !bad_frame_result.accepted,
          "bad frame must fail explicitly without persistence");

  common::GameState incomplete = fixture.state;
  incomplete.current_offer->recognitions[2U].reset();
  const auto incomplete_result = runtime->AcceptOffer(
      incomplete, fixture.card_outputs, fixture.frame, fixture.rois,
      fixture.ids[1U]);
  Require(incomplete_result.status.code == SessionRuntimeError::InvalidArgument &&
              !incomplete_result.accepted,
          "incomplete stable offer must fail explicitly without persistence");

  const AcceptedOfferResult accepted = runtime->AcceptOffer(
      fixture.state, fixture.card_outputs, fixture.frame, fixture.rois,
      fixture.ids[1U]);
  RequireStatus(accepted.status, "accept stable offer");
  Require(accepted.accepted && !accepted.fingerprint.empty(),
          "first stable offer must be accepted with a fingerprint");
  Require(accepted.stdout_json.find("\"current_offer\":{") !=
              std::string::npos &&
              accepted.stdout_json.find("\"display_name\":null") !=
                  std::string::npos,
          "stdout offer JSON must preserve the structured offer and null fields");

  const auto duplicate = runtime->AcceptOffer(
      fixture.state, fixture.card_outputs, fixture.frame, fixture.rois,
      fixture.ids[1U]);
  Require(duplicate.status.IsDuplicate() && !duplicate.accepted &&
              duplicate.fingerprint == accepted.fingerprint &&
              !duplicate.artifacts.has_value(),
          "same fingerprint must be idempotently rejected without artifacts");

  StableOfferFixture refreshed = fixture;
  for (std::size_t index = 0U; index < refreshed.ids.size(); ++index) {
    refreshed.ids[index] += "-refreshed";
    refreshed.state.current_offer->recognitions[index]->augment_id =
        refreshed.ids[index];
    refreshed.card_outputs[index].augment_id = refreshed.ids[index];
  }
  const auto round_conflict = runtime->AcceptOffer(
      refreshed.state, refreshed.card_outputs, refreshed.frame, refreshed.rois,
      refreshed.ids[1U]);
  Require(
      round_conflict.status.code == SessionRuntimeError::OfferRoundConflict &&
          !round_conflict.accepted,
      "different refreshed cards in one round must request worker replacement");

  const auto output_directory = runtime->OutputDirectory();
  const auto database_path = runtime->DatabasePath();
  const auto jsonl_path = runtime->JsonlPath();
  RequireStatus(runtime->Close(), "first Close");
  RequireStatus(runtime->Close(), "idempotent second Close");
  Require(!runtime->IsOpen(), "runtime must report closed after Close");
  const auto after_close = runtime->AcceptOffer(
      fixture.state, fixture.card_outputs, fixture.frame, fixture.rois,
      fixture.ids[1U]);
  Require(after_close.status.code == SessionRuntimeError::Closed,
          "AcceptOffer after Close must return Closed");

  ValidateDatabase(database_path);
  ValidateJsonl(jsonl_path, "阿狸");
  ValidatePngs(accepted, fixture.frame);
  Require(accepted.artifacts->raw_frame.parent_path() == output_directory,
          "artifacts must be written directly into the session directory");

  std::cout << "SESSION_ID=" << runtime->SessionId() << '\n';
  std::cout << "SESSION_DIR=" << PathUtf8(output_directory) << '\n';
  std::cout << "DATABASE=" << PathUtf8(database_path) << '\n';
  std::cout << "JSONL=" << PathUtf8(jsonl_path) << '\n';
  std::cout << "RAW=" << PathUtf8(accepted.artifacts->raw_frame) << '\n';
  std::cout << "LEFT=" << PathUtf8(accepted.artifacts->card_rois[0U]) << '\n';
  std::cout << "CENTER=" << PathUtf8(accepted.artifacts->card_rois[1U]) << '\n';
  std::cout << "RIGHT=" << PathUtf8(accepted.artifacts->card_rois[2U]) << '\n';
  std::cout << "SIDECAR=" << PathUtf8(accepted.artifacts->sidecar_json) << '\n';
}

}  // namespace

int main() {
  try {
    winrt::init_apartment(winrt::apartment_type::single_threaded);
    const std::filesystem::path tmp_root = LOL_SESSION_ADAPTER_TMP_ROOT;
    const std::filesystem::path runtime_root = LOL_SESSION_RUNTIME_ROOT;
    std::filesystem::create_directories(tmp_root);
    std::filesystem::create_directories(runtime_root);

    TestPathRejection(runtime_root);
    const auto catalog = lol_assistant::knowledge::LoadAugmentCatalog(
        LOL_ASSISTANT_CATALOG_PATH);
    Require(catalog.ok(), "real augment catalog must load for integration test");
    TestSessionLifecycle(runtime_root, *catalog.catalog);
    std::cout << "[PASS] session runtime persistence adapter integration\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "[FAIL] session runtime integration: " << error.what() << '\n';
    return 1;
  }
}
