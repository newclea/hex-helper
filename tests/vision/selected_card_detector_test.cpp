#include "selected_card_detector.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include "lol_assistant/replay/wic_image_codec.h"

#ifndef LOL_SELECTED_CARD_REAL_DATA_ROOT
#error LOL_SELECTED_CARD_REAL_DATA_ROOT must name the real augment dataset root
#endif

#ifndef LOL_SELECTED_CARD_HUD_AUDIT_ROOT
#error LOL_SELECTED_CARD_HUD_AUDIT_ROOT must name the real HUD audit crop root
#endif

namespace {

namespace common = lol_assistant::common;
namespace detector = lol_assistant::detector;
namespace replay = lol_assistant::replay;
namespace vision = lol_assistant::vision;

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

struct RealStage final {
  std::wstring_view sample_directory{};
  std::wstring_view audit_filename{};
  std::string_view captured_at_utc{};
};

constexpr std::array kStages{
    RealStage{L"sample_1788027776353375_f164_p37916_n0_"
              L"c09e2b6e69649767",
              L"stage1.png", "2026-08-29T18:22:56.353375Z"},
    RealStage{L"sample_1788027993161439_f4840_p37916_n1_"
              L"29931c2f04e0b70d",
              L"stage2.png", "2026-08-29T18:26:33.161439Z"},
    RealStage{L"sample_1788028378615354_f13067_p37916_n2_"
              L"38467560bb4f34a8",
              L"stage3.png", "2026-08-29T18:32:58.615354Z"},
};

constexpr std::uint64_t kBangBangHash = 0x0026101446110100ULL;
constexpr std::uint64_t kBloodMoneyBurnHash = 0x0041454545100200ULL;

[[nodiscard]] std::filesystem::path DatasetRoot() {
  return std::filesystem::path{LOL_SELECTED_CARD_REAL_DATA_ROOT};
}

[[nodiscard]] std::filesystem::path AuditRoot() {
  return std::filesystem::path{LOL_SELECTED_CARD_HUD_AUDIT_ROOT};
}

[[nodiscard]] common::Frame Decode(const std::filesystem::path& path,
                                   const std::uint64_t frame_id) {
  return replay::WicImageCodec::Decode(
      path, {common::FrameSourceKind::Replay, "selected-card-real-replay"},
      frame_id);
}

[[nodiscard]] common::Frame LoadRaw(const std::size_t stage_index) {
  return Decode(DatasetRoot() / kStages[stage_index].sample_directory /
                    L"RAW.png",
                stage_index + 1U);
}

[[nodiscard]] std::string ReadText(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    return {};
  }
  return {std::istreambuf_iterator<char>{input},
          std::istreambuf_iterator<char>{}};
}

[[nodiscard]] std::vector<vision::IconHashTemplate> Stage1OfferTemplates() {
  return {{"ARAM_EtherealWeapon", 0x001442550d091200ULL,
           {"KIWI", "KIWI_JADE"}},
          {"DarkWind", 0x00040c14132b1400ULL, {"KIWI", "KIWI_JADE"}},
          {"BloodMoneyBurn", kBloodMoneyBurnHash,
           {"KIWI", "KIWI_JADE"}}};
}

[[nodiscard]] std::vector<vision::IconHashTemplate> Stage2OfferTemplates() {
  return {{"ARAM_BangBang", kBangBangHash, {"KIWI", "KIWI_JADE"}},
          {"ARAM_ApexInventor", 0x0014020141320400ULL,
           {"KIWI", "KIWI_JADE"}},
          {"ARAM_Upgrade_Sheen", 0x0006014549100600ULL,
           {"KIWI", "KIWI_JADE"}}};
}

[[nodiscard]] std::string Hex(const std::optional<std::uint64_t> value) {
  if (!value.has_value()) {
    return "none";
  }
  std::ostringstream output;
  output << std::hex << std::setfill('0') << std::setw(16) << *value;
  return output.str();
}

void PrintResult(const std::string_view label,
                 const vision::SelectedCardDetectionResult& result) {
  std::cout << "REAL " << label << " slot=";
  if (result.hud_slot_index.has_value()) {
    std::cout << *result.hud_slot_index;
  } else {
    std::cout << "none";
  }
  std::cout << " roi=";
  if (result.icon_roi.has_value()) {
    std::cout << result.icon_roi->x << ',' << result.icon_roi->y << ','
              << result.icon_roi->width << ',' << result.icon_roi->height;
  } else {
    std::cout << "none";
  }
  std::cout << " hash=" << Hex(result.observed_difference_hash)
            << " luma=" << static_cast<unsigned>(result.appearance.minimum_luma)
            << ".."
            << static_cast<unsigned>(result.appearance.maximum_luma)
            << " edge=" << std::fixed << std::setprecision(6)
            << result.appearance.edge_fraction << " change="
            << result.mean_absolute_luma_change << " top1=";
  if (result.icon_match.top1_score.has_value()) {
    std::cout << *result.icon_match.top1_score;
  } else {
    std::cout << "none";
  }
  std::cout << " margin=";
  if (result.icon_match.margin.has_value()) {
    std::cout << *result.icon_match.margin;
  } else {
    std::cout << "none";
  }
  std::cout << " id=" << result.candidate_id.value_or("UNKNOWN")
            << " reason=" << result.reason << '\n';
}

[[nodiscard]] double AuditCropMeanAbsoluteRgbDelta(
    const common::Frame& raw, const common::Frame& audit) {
  constexpr detector::PixelRoi kAuditRoi{360U, 1280U, 360U, 320U};
  const auto raw_crop = detector::CropRawBgraOwning(raw, kAuditRoi);
  if (!raw_crop.ok() || audit.width != kAuditRoi.width ||
      audit.height != kAuditRoi.height) {
    return std::numeric_limits<double>::infinity();
  }
  std::uint64_t absolute_delta_sum = 0U;
  for (std::uint32_t y = 0U; y < audit.height; ++y) {
    for (std::uint32_t x = 0U; x < audit.width; ++x) {
      const auto raw_offset = static_cast<std::size_t>(y) *
                                  raw_crop.value->stride +
                              static_cast<std::size_t>(x) * 4U;
      const auto audit_offset = static_cast<std::size_t>(y) * audit.stride +
                                static_cast<std::size_t>(x) * 4U;
      for (std::size_t channel = 0U; channel < 3U; ++channel) {
        const auto left = raw_crop.value->pixels[raw_offset + channel];
        const auto right = audit.buffer[audit_offset + channel];
        absolute_delta_sum += left >= right ? left - right : right - left;
      }
    }
  }
  const auto channel_count = static_cast<double>(audit.width) * audit.height *
                             3.0;
  return static_cast<double>(absolute_delta_sum) / channel_count;
}

void TestMeasuredGeometryAndAuditCrops() {
  constexpr std::string_view test = "real HUD geometry and audit crops";
  const auto rois = vision::ComputeHudOwnedSlotRois(2560U, 1600U);
  CHECK(test, rois.ok());
  const std::array expected{
      detector::PixelRoi{398U, 1408U, 79U, 79U},
      detector::PixelRoi{488U, 1408U, 79U, 79U},
      detector::PixelRoi{398U, 1503U, 79U, 79U},
      detector::PixelRoi{488U, 1503U, 79U, 79U},
  };
  CHECK(test, rois.value.has_value() && *rois.value == expected);

  // Geometry-only inference from the measured 16:10 canvas. This is not
  // presented as real 16:9 recognition accuracy; it guards against the old
  // full-width normalization drift and hard rejection.
  const auto widescreen_rois = vision::ComputeHudOwnedSlotRois(1920U, 1080U);
  const std::array expected_widescreen{
      detector::PixelRoi{269U, 950U, 53U, 53U},
      detector::PixelRoi{329U, 950U, 53U, 53U},
      detector::PixelRoi{269U, 1015U, 53U, 53U},
      detector::PixelRoi{329U, 1015U, 53U, 53U},
  };
  CHECK(test, widescreen_rois.ok());
  CHECK(test, widescreen_rois.value.has_value() &&
                  *widescreen_rois.value == expected_widescreen);

  for (std::size_t index = 0U; index < kStages.size(); ++index) {
    const auto raw = LoadRaw(index);
    const auto audit = Decode(AuditRoot() / kStages[index].audit_filename,
                              index + 101U);
    CHECK(test, raw.width == 2560U && raw.height == 1600U);
    CHECK(test, audit.width == 360U && audit.height == 320U);
    const double mean_delta = AuditCropMeanAbsoluteRgbDelta(raw, audit);
    std::cout << "REAL audit stage=" << index + 1U
              << " raw_roi=360,1280,360,320 normalized=0.140625,0.8,"
                 "0.140625,0.2 mean_abs_rgb_delta="
              << std::fixed << std::setprecision(6) << mean_delta << '\n';
    CHECK(test, mean_delta < 2.0);

    const auto metadata = ReadText(DatasetRoot() /
                                   kStages[index].sample_directory /
                                   L"metadata.json");
    CHECK(test, metadata.find(kStages[index].captured_at_utc) !=
                    std::string::npos);
    CHECK(test, metadata.find("\"resolution\":{\"width\":2560,"
                              "\"height\":1600") != std::string::npos);
  }
}

void TestRealHudSelectionReplay() {
  constexpr std::string_view test = "real HUD selected-card replay";
  const vision::SelectedCardDetector stage1_detector(Stage1OfferTemplates(),
                                                       {}, "KIWI");
  const vision::SelectedCardDetector stage2_detector(Stage2OfferTemplates(),
                                                       {}, "KIWI");

  const auto stage1 = LoadRaw(0U);
  const auto before_first_choice =
      stage1_detector.DetectNextOwned(stage1, stage1, 0U);
  PrintResult("stage1.next0.placeholder", before_first_choice);
  CHECK(test, !before_first_choice.identified());
  CHECK(test, !before_first_choice.candidate_id.has_value());

  const auto stage2 = LoadRaw(1U);
  const auto first_owned =
      stage1_detector.DetectNextOwned(stage1, stage2, 0U);
  PrintResult("stage2.next0", first_owned);
  CHECK(test, first_owned.identified());
  CHECK(test, first_owned.candidate_id == "BloodMoneyBurn");
  const auto stage2_placeholder =
      stage2_detector.DetectNextOwned(stage1, stage2, 1U);
  PrintResult("stage2.next1.placeholder", stage2_placeholder);
  CHECK(test, !stage2_placeholder.identified());

  const auto stage3 = LoadRaw(2U);
  const auto second_owned =
      stage2_detector.DetectNextOwned(stage2, stage3, 1U);
  PrintResult("stage3.next1", second_owned);
  CHECK(test, second_owned.identified());
  CHECK(test, second_owned.candidate_id == "ARAM_BangBang");
  const auto stage3_placeholder =
      stage2_detector.DetectNextOwned(stage2, stage3, 2U);
  PrintResult("stage3.next2.placeholder", stage3_placeholder);
  CHECK(test, !stage3_placeholder.identified());

  const auto black_slot =
      stage1_detector.DetectNextOwned(stage1, stage1, 3U);
  PrintResult("stage1.slot3.empty", black_slot);
  CHECK(test, !black_slot.identified());
  const auto out_of_range =
      stage2_detector.DetectNextOwned(stage3, stage3, 4U);
  CHECK(test, !out_of_range.identified());
  CHECK(test, out_of_range.reason == "next_owned_slot_out_of_range");

  const auto stale_first_owned =
      stage1_detector.DetectNextOwned(stage2, stage3, 0U);
  PrintResult("stage3.slot0.stale", stale_first_owned);
  CHECK(test, !stale_first_owned.identified());
  CHECK(test, stale_first_owned.reason == "next_owned_slot_was_not_empty");

}

void TestRealCropTemplateConflicts() {
  constexpr std::string_view test =
      "real HUD crop template conflicts";
  const auto stage2 = LoadRaw(1U);
  const vision::SelectedCardDetector baseline(Stage1OfferTemplates(), {},
                                               "KIWI");
  const auto stage1 = LoadRaw(0U);
  const auto baseline_result = baseline.DetectNextOwned(stage1, stage2, 0U);
  CHECK(test, baseline_result.observed_difference_hash.has_value());
  if (!baseline_result.observed_difference_hash.has_value()) {
    return;
  }

  // Real product-manifest collision: both IDs have dHash 0041454545100200.
  // The pixels are the real stage2 HUD crop. Equal top1 must never leak an ID.
  std::vector<vision::IconHashTemplate> conflicting{
      {"BloodMoneyBurn", kBloodMoneyBurnHash, {"KIWI"}},
      {"ARAM_Quest_VoidImmolation", kBloodMoneyBurnHash, {"KIWI"}},
  };
  const vision::SelectedCardDetector conflict_detector(
      std::move(conflicting), {}, "KIWI");
  const auto conflict =
      conflict_detector.DetectNextOwned(stage1, stage2, 0U);
  CHECK(test, !conflict.identified());
  CHECK(test, !conflict.candidate_id.has_value());
  CHECK(test, conflict.icon_match.state == vision::IconMatchState::Unknown);
  CHECK(test, conflict.icon_match.reason == "hash_top1_ambiguous");
  CHECK(test,
        conflict.reason ==
            "icon_not_unique_high_confidence:hash_top1_ambiguous");
  std::cout << "REAL-MANIFEST-CONFLICT candidate=UNKNOWN reason="
            << conflict.reason << '\n';

  // SYNTHETIC TEMPLATE SET, REAL PIXELS: force an exact equal-score tie to
  // preserve fail-closed behavior independently of today's product manifest.
  const auto observed_hash = *baseline_result.observed_difference_hash;
  std::vector<vision::IconHashTemplate> synthetic_conflicting{
      {"synthetic_conflict_a", observed_hash, {"KIWI"}},
      {"synthetic_conflict_b", observed_hash, {"KIWI"}},
  };
  const vision::SelectedCardDetector synthetic_conflict_detector(
      std::move(synthetic_conflicting), {}, "KIWI");
  const auto synthetic_conflict =
      synthetic_conflict_detector.DetectNextOwned(stage1, stage2, 0U);
  CHECK(test, !synthetic_conflict.identified());
  CHECK(test, !synthetic_conflict.candidate_id.has_value());
  CHECK(test,
        synthetic_conflict.icon_match.state == vision::IconMatchState::Unknown);
  CHECK(test, synthetic_conflict.icon_match.reason == "hash_top1_ambiguous");
  std::cout << "SYNTHETIC-TEMPLATES real_pixels=true candidate=UNKNOWN reason="
            << synthetic_conflict.reason << '\n';
}

}  // namespace

int main() {
  TestMeasuredGeometryAndAuditCrops();
  TestRealHudSelectionReplay();
  TestRealCropTemplateConflicts();
  std::cout << "selected_card_detector checks=" << g_checks
            << " failures=" << g_failures << '\n';
  return g_failures == 0 ? 0 : 1;
}
