#include "lol_assistant/collection/sample_collector.h"

#include <Windows.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "lol_assistant/replay/wic_image_codec.h"

namespace {

using namespace std::chrono_literals;
namespace collection = lol_assistant::collection;
namespace common = lol_assistant::common;
namespace detector = lol_assistant::detector;
namespace replay = lol_assistant::replay;

int g_failures = 0;
int g_checks = 0;
int g_tests = 0;

void Check(const bool condition, const std::string_view expression,
           const std::string_view test) {
  ++g_checks;
  if (!condition) {
    ++g_failures;
    std::cerr << "[FAIL] " << test << ": " << expression << '\n';
  }
}

#define CHECK(test, expression) Check((expression), #expression, (test))

[[nodiscard]] bool IsBelow(const std::filesystem::path& root,
                           const std::filesystem::path& candidate) {
  const auto relative = candidate.lexically_normal().lexically_relative(root);
  if (relative.empty() || relative.is_absolute()) {
    return false;
  }
  for (const auto& component : relative) {
    if (component == L"..") {
      return false;
    }
  }
  return true;
}

[[nodiscard]] common::Frame MakeFrame(const bool increasing,
                                      const std::uint8_t offset,
                                      const std::uint64_t frame_id) {
  common::Frame frame;
  frame.source = {common::FrameSourceKind::Replay,
                  "sample-collector-synthetic"};
  frame.frame_id = frame_id;
  frame.width = 90U;
  frame.height = 48U;
  frame.stride = frame.width * 4U;
  frame.timestamps.captured_at_utc =
      common::UtcTimestamp{std::chrono::seconds{1'700'000'000}};
  frame.buffer.resize(static_cast<std::size_t>(frame.stride) * frame.height);
  for (std::uint32_t y = 0U; y < frame.height; ++y) {
    for (std::uint32_t x = 0U; x < frame.width; ++x) {
      const std::uint32_t base = increasing ? x * 2U : 255U - x * 2U;
      const auto luma = static_cast<std::uint8_t>(
          (std::min)(255U, base + static_cast<std::uint32_t>(offset)));
      const auto pixel = static_cast<std::size_t>(y) * frame.stride +
                         static_cast<std::size_t>(x) * 4U;
      frame.buffer[pixel + 0U] = luma;
      frame.buffer[pixel + 1U] = luma;
      frame.buffer[pixel + 2U] = luma;
      frame.buffer[pixel + 3U] = 255U;
    }
  }
  return frame;
}

[[nodiscard]] collection::SampleCollectionRequest MakeRequest(
    const common::MonotonicTimestamp dedup_time) {
  collection::SampleCollectionRequest request;
  request.sample_kind = collection::SampleKind::Unknown;
  request.capture_reason = collection::CaptureReason::DetectorSuspect;
  request.capture_reason_detail = "unsupported_2560x1600_candidate";
  request.timestamp =
      common::UtcTimestamp{std::chrono::seconds{1'700'000'000}};
  request.dedup_observed_at = dedup_time;
  request.ui_scale = std::nullopt;
  request.detector = {false, 0.37F, "unsupported_aspect_ratio"};
  request.rois.offer = {0U, 0U, 90U, 48U};
  request.rois.cards = {
      collection::CardRoiMetadata{{0U, 0U, 28U, 48U},
                                  detector::PixelRoi{5U, 5U, 18U, 8U},
                                  detector::PixelRoi{2U, 20U, 10U, 10U}},
      collection::CardRoiMetadata{{31U, 0U, 28U, 48U},
                                  detector::PixelRoi{36U, 5U, 18U, 8U},
                                  detector::PixelRoi{33U, 20U, 10U, 10U}},
      collection::CardRoiMetadata{{62U, 0U, 28U, 48U},
                                  detector::PixelRoi{67U, 5U, 18U, 8U},
                                  detector::PixelRoi{64U, 20U, 10U, 10U}}};
  request.cards[0U] = {"候选\n文本", {"候选", "文本"}, std::nullopt,
                       collection::OcrSampleStatus::Unknown, 0.42F};
  request.cards[1U] = {"全心为你", {"全心为你"}, "ARAM_AllForYou",
                       collection::OcrSampleStatus::Matched, 0.91F};
  request.cards[2U] = {"", {}, std::nullopt,
                       collection::OcrSampleStatus::NotRun, std::nullopt};
  return request;
}

[[nodiscard]] std::string ReadText(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  return {std::istreambuf_iterator<char>{input},
          std::istreambuf_iterator<char>{}};
}

[[nodiscard]] std::size_t EntryCount(const std::filesystem::path& path) {
  std::size_t count = 0U;
  for ([[maybe_unused]] const auto& entry :
       std::filesystem::directory_iterator(path)) {
    ++count;
  }
  return count;
}

void TestUnknownBundleAndMetadata(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test =
      "unknown sample saves an atomic five-file parseable bundle";
  const auto dataset = root / L"unknown_bundle";
  collection::SampleCollector collector({dataset, {10s, 0U, true, false}});
  const auto frame = MakeFrame(true, 0U, 10U);
  const auto base = common::MonotonicTimestamp{100s};
  const auto result = collector.Collect(frame, MakeRequest(base));

  CHECK(test, result.saved());
  CHECK(test, IsBelow(collector.dataset_root(), result.sample_directory));
  CHECK(test, EntryCount(result.sample_directory) == 5U);
  const std::set<std::wstring> expected{
      L"RAW.png", L"LEFT_CARD.png", L"CENTER_CARD.png", L"RIGHT_CARD.png",
      L"metadata.json"};
  std::set<std::wstring> actual;
  for (const auto& entry :
       std::filesystem::directory_iterator(result.sample_directory)) {
    actual.insert(entry.path().filename().wstring());
    CHECK(test, entry.is_regular_file());
    CHECK(test, entry.file_size() > 0U);
  }
  CHECK(test, actual == expected);

  const auto raw = replay::WicImageCodec::Decode(
      result.sample_directory / L"RAW.png",
      {common::FrameSourceKind::Replay, "verify-raw"});
  CHECK(test, raw.width == frame.width && raw.height == frame.height);
  constexpr std::array<std::wstring_view, 3U> card_files{
      L"LEFT_CARD.png", L"CENTER_CARD.png", L"RIGHT_CARD.png"};
  for (const auto filename : card_files) {
    const auto card = replay::WicImageCodec::Decode(
        result.sample_directory / filename,
        {common::FrameSourceKind::Replay, "verify-card"});
    CHECK(test, card.width == 28U && card.height == 48U);
  }

  const std::string metadata =
      ReadText(result.sample_directory / L"metadata.json");
  CHECK(test, collection::IsMetadataJsonParseable(metadata));
  CHECK(test, !collection::IsMetadataJsonParseable("{]"));
  CHECK(test,
        metadata.find("\"schema\":\"lol_assistant.sample_collection\"") !=
            std::string::npos);
  CHECK(test, metadata.find("\"version\":1") != std::string::npos);
  CHECK(test, metadata.find("\"schema_version\":1") != std::string::npos);
  CHECK(test,
        metadata.find("\"sample_kind\":\"unknown\"") != std::string::npos);
  CHECK(test,
        metadata.find("\"provenance\":\"unknown\"") != std::string::npos);
  CHECK(test,
        metadata.find("\"reference\":\"sample-collector-synthetic\"") !=
            std::string::npos);
  CHECK(test,
        metadata.find("\"captured_at_utc\":") != std::string::npos);
  CHECK(test,
        metadata.find("\"capture_reason\":\"detector_suspect\"") !=
            std::string::npos);
  CHECK(test,
        metadata.find("\"ui_scale\":null") != std::string::npos);
  CHECK(test,
        metadata.find("\"matched_id\":\"ARAM_AllForYou\"") !=
            std::string::npos);
  CHECK(test,
        metadata.find("\"title\":{") != std::string::npos &&
            metadata.find("\"icon\":{") != std::string::npos);
}

void TestDedupChangedFrameAndManual(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test =
      "exact dHash interval and manual F8 policies are explicit";
  collection::SampleCollector collector(
      {root / L"dedup", {10s, 0U, true, false}});
  const auto base = common::MonotonicTimestamp{200s};

  const auto increasing = MakeFrame(true, 0U, 20U);
  auto request = MakeRequest(base);
  const auto first = collector.Collect(increasing, request);
  CHECK(test, first.saved());

  auto same_bytes = increasing;
  same_bytes.frame_id = 21U;
  request.dedup_observed_at = base + 1ms;
  const auto exact = collector.Collect(same_bytes, request);
  CHECK(test, exact.duplicate());
  CHECK(test, exact.message == "duplicate_exact_raw_bytes");
  CHECK(test, exact.sample_directory == first.sample_directory);

  const auto changed = MakeFrame(false, 0U, 22U);
  request.dedup_observed_at = base + 2ms;
  const auto changed_result = collector.Collect(changed, request);
  CHECK(test, changed_result.saved());
  CHECK(test, changed_result.sample_directory != first.sample_directory);

  const auto similar_changed_bytes = MakeFrame(false, 1U, 23U);
  request.dedup_observed_at = base + 3ms;
  const auto similar = collector.Collect(similar_changed_bytes, request);
  CHECK(test, similar.duplicate());
  CHECK(test,
        similar.message ==
            "duplicate_similar_dhash_inside_minimum_interval");

  request.capture_reason = collection::CaptureReason::ManualF8;
  request.dedup_observed_at = base + 4ms;
  const auto manual = collector.Collect(similar_changed_bytes, request);
  CHECK(test, manual.saved());

  request.dedup_observed_at = base + 5ms;
  const auto manual_exact = collector.Collect(similar_changed_bytes, request);
  CHECK(test, manual_exact.duplicate());
  CHECK(test, manual_exact.message == "duplicate_exact_raw_bytes");

  const auto elapsed_similar = MakeFrame(false, 2U, 24U);
  request.capture_reason = collection::CaptureReason::DetectorSuspect;
  request.dedup_observed_at = base + 11s;
  const auto after_interval = collector.Collect(elapsed_similar, request);
  CHECK(test, after_interval.saved());
}

class FailingImageWriter final : public collection::ISampleImageWriter {
 public:
  explicit FailingImageWriter(std::filesystem::path root)
      : root_(std::move(root)) {}

  void SavePng(const common::Frame& frame,
               const std::filesystem::path& path) const override {
    all_paths_inside_ = all_paths_inside_ && IsBelow(root_, path);
    ++calls_;
    if (calls_ == 3U) {
      throw std::runtime_error("injected PNG failure");
    }
    replay::WicImageCodec::SavePng(frame, path);
  }

  [[nodiscard]] bool all_paths_inside() const noexcept {
    return all_paths_inside_;
  }

 private:
  std::filesystem::path root_;
  mutable std::size_t calls_{0U};
  mutable bool all_paths_inside_{true};
};

void TestFailureRollback(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test =
      "partial write failure rolls back the owned staging directory";
  const auto dataset = root / L"rollback";
  const auto writer = std::make_shared<FailingImageWriter>(dataset);
  collection::SampleCollector collector(
      {dataset, {10s, 0U, true, false}}, writer);
  const auto result = collector.Collect(
      MakeFrame(true, 0U, 30U),
      MakeRequest(common::MonotonicTimestamp{300s}));
  CHECK(test, result.status == collection::CollectStatus::IoError);
  CHECK(test, result.sample_directory.empty());
  CHECK(test, result.message.find("injected PNG failure") != std::string::npos);
  CHECK(test, writer->all_paths_inside());
  CHECK(test, EntryCount(dataset) == 0U);
}

template <typename Callable>
[[nodiscard]] bool Throws(const Callable& callable) {
  try {
    callable();
    return false;
  } catch (...) {
    return true;
  }
}

void TestRootBoundaryAndTruthfulKind(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test =
      "dataset root and real sample labels fail closed";
  CHECK(test, Throws([] {
          collection::SampleCollector invalid(
              {L"relative_dataset", {10s, 0U, true, false}});
        }));
  CHECK(test, Throws([&root] {
          collection::SampleCollector invalid(
              {root / L"nested" / L".." / L"escape",
               {10s, 0U, true, false}});
        }));
  CHECK(test, Throws([&root] {
          collection::SampleCollector invalid(
              {root / L"invalid_threshold", {10s, 65U, true, false}});
        }));

  const auto dataset = root / L"truthful_kind";
  collection::SampleCollector collector(
      {dataset, {10s, 0U, true, false}});
  auto request = MakeRequest(common::MonotonicTimestamp{400s});
  request.sample_kind = collection::SampleKind::Real;
  request.window.id = "test-window";
  const auto result = collector.Collect(MakeFrame(true, 0U, 40U), request);
  CHECK(test, result.status == collection::CollectStatus::InvalidRequest);
  CHECK(test,
        result.message == "real samples require a live capture frame source");
  CHECK(test, EntryCount(dataset) == 0U);

  auto live_frame = MakeFrame(true, 1U, 41U);
  live_frame.source = {common::FrameSourceKind::WindowsGraphicsCapture,
                       "contract-wgc-window"};
  auto live_request = MakeRequest(common::MonotonicTimestamp{401s});
  live_request.window.id = "test-window";
  const auto wrong_live_kind = collector.Collect(live_frame, live_request);
  CHECK(test,
        wrong_live_kind.status == collection::CollectStatus::InvalidRequest);
  CHECK(test,
        wrong_live_kind.message ==
            "live capture frame sources must use real provenance");

  collection::SampleCollector synthetic_bucket(
      {root / L"synthetic", {10s, 0U, true, false}});
  auto unknown_request = MakeRequest(common::MonotonicTimestamp{402s});
  const auto wrong_bucket = synthetic_bucket.Collect(
      MakeFrame(false, 2U, 42U), unknown_request);
  CHECK(test,
        wrong_bucket.status == collection::CollectStatus::InvalidRequest);
  CHECK(test,
        wrong_bucket.message ==
            "sample provenance does not match dataset_root bucket");

  unknown_request.sample_kind = collection::SampleKind::Synthetic;
  const auto synthetic = synthetic_bucket.Collect(
      MakeFrame(false, 3U, 43U), unknown_request);
  CHECK(test, synthetic.saved());
  const std::string synthetic_metadata =
      ReadText(synthetic.sample_directory / L"metadata.json");
  CHECK(test,
        synthetic_metadata.find("\"provenance\":\"synthetic\"") !=
            std::string::npos);
  CHECK(test,
        synthetic_metadata.find("\"kind\":\"replay\"") !=
            std::string::npos);
}

}  // namespace

int main() {
  try {
    const auto unique =
        std::to_wstring(GetCurrentProcessId()) + L"_" +
        std::to_wstring(std::chrono::steady_clock::now()
                            .time_since_epoch()
                            .count());
    const std::filesystem::path root =
        std::filesystem::path(LOL_COLLECTION_TEST_ROOT) /
        (L"sample_collector_" + unique);
    TestUnknownBundleAndMetadata(root);
    TestDedupChangedFrameAndManual(root);
    TestFailureRollback(root);
    TestRootBoundaryAndTruthfulKind(root);
  } catch (const std::exception& error) {
    ++g_failures;
    std::cerr << "[UNCAUGHT] " << error.what() << '\n';
  }

  std::cout << "sample collector tests=" << g_tests << " checks=" << g_checks
            << " failures=" << g_failures << '\n';
  return g_failures == 0 ? 0 : 1;
}
