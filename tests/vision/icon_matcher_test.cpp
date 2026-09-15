#include "lol_assistant/vision/icon_matcher.h"

#include <charconv>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "lol_assistant/replay/wic_image_codec.h"
#include "lol_assistant/vision/text_matcher.h"

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

[[nodiscard]] lol_assistant::detector::OwningBgraCrop MakeHorizontalGradient(
    const std::uint32_t width, const std::uint32_t height,
    const bool ascending, const std::uint8_t brightness_offset = 0U) {
  lol_assistant::detector::OwningBgraCrop crop;
  crop.width = width;
  crop.height = height;
  crop.stride = width * 4U;
  crop.pixels.resize(static_cast<std::size_t>(crop.stride) * height);
  for (std::uint32_t y = 0U; y < height; ++y) {
    for (std::uint32_t x = 0U; x < width; ++x) {
      const std::uint32_t position =
          width > 1U ? (x * 120U) / (width - 1U) : 0U;
      const std::uint32_t value =
          static_cast<std::uint32_t>(brightness_offset) +
          (ascending ? position : 120U - position);
      const auto offset = static_cast<std::size_t>(y) * crop.stride +
                          static_cast<std::size_t>(x) * 4U;
      crop.pixels[offset + 0U] = static_cast<std::uint8_t>(value);
      crop.pixels[offset + 1U] = static_cast<std::uint8_t>(value);
      crop.pixels[offset + 2U] = static_cast<std::uint8_t>(value);
      crop.pixels[offset + 3U] = 255U;
    }
  }
  return crop;
}

[[nodiscard]] std::uint64_t HashOf(
    const lol_assistant::detector::OwningBgraCrop& crop,
    const std::string_view test) {
  const auto hash = lol_assistant::vision::ComputeDifferenceHash(crop);
  CHECK(test, hash.ok());
  return hash.hash.value_or(0U);
}

void TestSameImage() {
  constexpr std::string_view test = "same image";
  const auto crop = MakeHorizontalGradient(37U, 29U, true, 20U);
  const auto hash = HashOf(crop, test);
  const lol_assistant::vision::PerceptualHashTemplateMatcher matcher(
      {{"same", hash, {"KIWI"}}});
  const auto result = matcher.Match(crop, "KIWI");
  CHECK(test, result.state == lol_assistant::vision::IconMatchState::Matched);
  CHECK(test, result.augment_id == "same");
  CHECK(test, result.top1_score.has_value());
  CHECK(test, std::abs(*result.top1_score - 1.0F) < 0.0001F);
  CHECK(test, result.candidate_ids == std::vector<std::string>{"same"});
}

void TestResizeAndBrightnessRobustness() {
  constexpr std::string_view test = "resize and brightness";
  const auto source = MakeHorizontalGradient(19U, 13U, true, 10U);
  const auto resized_and_brighter =
      MakeHorizontalGradient(53U, 41U, true, 70U);
  const auto source_hash = HashOf(source, test);
  CHECK(test, HashOf(resized_and_brighter, test) == source_hash);
  const lol_assistant::vision::PerceptualHashTemplateMatcher matcher(
      {{"stable", source_hash, {"KIWI", "KIWI_JADE"}}});
  const auto result = matcher.Match(resized_and_brighter, "KIWI_JADE");
  CHECK(test, result.state == lol_assistant::vision::IconMatchState::Matched);
  CHECK(test, result.augment_id == "stable");
}

void TestDifferentImageRejected() {
  constexpr std::string_view test = "different image";
  const auto ascending = MakeHorizontalGradient(31U, 31U, true, 20U);
  const auto descending = MakeHorizontalGradient(31U, 31U, false, 20U);
  const lol_assistant::vision::PerceptualHashTemplateMatcher matcher(
      {{"ascending", HashOf(ascending, test), {"KIWI"}}});
  const auto result = matcher.Match(descending, "KIWI");
  CHECK(test, result.state == lol_assistant::vision::IconMatchState::Unknown);
  CHECK(test, result.reason == "hash_distance_above_threshold");
  CHECK(test, !result.augment_id.has_value());
}

void TestTop2Ambiguity() {
  constexpr std::string_view test = "top2 ambiguity";
  const auto crop = MakeHorizontalGradient(29U, 23U, true, 10U);
  const auto hash = HashOf(crop, test);
  const lol_assistant::vision::PerceptualHashTemplateMatcher matcher(
      {{"top1", hash, {"KIWI"}},
       {"top2", hash ^ std::uint64_t{1U}, {"KIWI"}}});
  const auto result = matcher.Match(crop, "KIWI");
  CHECK(test, result.state == lol_assistant::vision::IconMatchState::Unknown);
  CHECK(test, result.reason == "hash_margin_below_threshold");
  CHECK(test, result.top1_score.has_value());
  CHECK(test, result.top2_score.has_value());
  CHECK(test, result.margin.has_value());
  CHECK(test, std::abs(*result.margin - (1.0F / 64.0F)) < 0.0001F);
  CHECK(test, result.candidate_ids ==
                  (std::vector<std::string>{"top1", "top2"}));
}

void TestModeFilteringAndTiedIcons() {
  constexpr std::string_view test = "mode filtering";
  const auto crop = MakeHorizontalGradient(25U, 25U, true, 10U);
  const auto hash = HashOf(crop, test);
  const lol_assistant::vision::PerceptualHashTemplateMatcher matcher(
      {{"jade", hash, {"KIWI_JADE"}}, {"kiwi", hash, {"KIWI"}}});

  const auto kiwi = matcher.Match(crop, "KIWI");
  CHECK(test, kiwi.state == lol_assistant::vision::IconMatchState::Matched);
  CHECK(test, kiwi.augment_id == "kiwi");

  const auto jade = matcher.Match(crop, "KIWI_JADE");
  CHECK(test, jade.state == lol_assistant::vision::IconMatchState::Matched);
  CHECK(test, jade.augment_id == "jade");

  const auto all_modes = matcher.Match(crop);
  CHECK(test,
        all_modes.state == lol_assistant::vision::IconMatchState::Unknown);
  CHECK(test, all_modes.reason == "hash_top1_ambiguous");
  CHECK(test, all_modes.candidate_ids ==
                  (std::vector<std::string>{"jade", "kiwi"}));

  const auto unsupported = matcher.Match(crop, "CHERRY");
  CHECK(test, unsupported.state ==
                  lol_assistant::vision::IconMatchState::Unavailable);
  CHECK(test, unsupported.reason == "template_unavailable_for_mode");
}

void TestFusionPolicy() {
  constexpr std::string_view test = "fusion policy";
  lol_assistant::vision::TextMatchResult ocr;
  ocr.kind = lol_assistant::vision::TextMatchKind::Exact;
  ocr.id = "ocr-id";
  ocr.reason = "exact_match";

  lol_assistant::vision::IconMatchResult conflict_icon;
  conflict_icon.state = lol_assistant::vision::IconMatchState::Matched;
  conflict_icon.augment_id = "icon-id";
  conflict_icon.reason = "hash_match";
  const auto conflict =
      lol_assistant::vision::FuseAugmentIdentity(ocr, conflict_icon);
  CHECK(test, conflict.state ==
                  lol_assistant::vision::FusedAugmentState::Unknown);
  CHECK(test, !conflict.augment_id.has_value());
  CHECK(test, conflict.reason.starts_with("ocr_icon_conflict:"));

  conflict_icon.augment_id = "ocr-id";
  const auto confirmed =
      lol_assistant::vision::FuseAugmentIdentity(ocr, conflict_icon);
  CHECK(test, confirmed.confirmed());
  CHECK(test, confirmed.augment_id == "ocr-id");

  const lol_assistant::vision::IconMatchResult unavailable_icon;
  const auto tentative =
      lol_assistant::vision::FuseAugmentIdentity(ocr, unavailable_icon);
  CHECK(test, tentative.state ==
                  lol_assistant::vision::FusedAugmentState::Tentative);
  CHECK(test, tentative.augment_id == "ocr-id");
  CHECK(test, tentative.reason == "ocr_only_icon_unavailable");
}

void TestNoTemplates() {
  constexpr std::string_view test = "no templates";
  const auto crop = MakeHorizontalGradient(9U, 8U, true, 10U);
  const lol_assistant::vision::PerceptualHashTemplateMatcher matcher;
  const auto result = matcher.Match(crop, "KIWI");
  CHECK(test,
        result.state == lol_assistant::vision::IconMatchState::Unavailable);
  CHECK(test, !result.available());
  CHECK(test, result.reason == "template_unavailable");
  CHECK(test, !result.top1_score.has_value());
  CHECK(test, result.candidate_ids.empty());
}

[[nodiscard]] std::optional<std::uint64_t> ParseHexHash(
    const std::string_view value) {
  std::uint64_t parsed = 0U;
  const auto result = std::from_chars(value.data(), value.data() + value.size(),
                                      parsed, 16);
  if (result.ec != std::errc{} || result.ptr != value.data() + value.size()) {
    return std::nullopt;
  }
  return parsed;
}

void TestWicManifestSelfMatch(const std::filesystem::path& image_path,
                              const std::string_view expected_hex_hash) {
  constexpr std::string_view test = "WIC manifest template self-match";
  const auto expected = ParseHexHash(expected_hex_hash);
  CHECK(test, expected.has_value());
  if (!expected.has_value()) {
    return;
  }

  const auto frame = lol_assistant::replay::WicImageCodec::Decode(
      image_path,
      {lol_assistant::common::FrameSourceKind::Replay, "augment-icon-template"});
  lol_assistant::detector::OwningBgraCrop crop;
  crop.width = frame.width;
  crop.height = frame.height;
  crop.stride = frame.stride;
  crop.pixels = frame.buffer;
  const auto actual = lol_assistant::vision::ComputeDifferenceHash(crop);
  CHECK(test, actual.hash == expected);

  const lol_assistant::vision::PerceptualHashTemplateMatcher matcher(
      {{"manifest-template", *expected, {"KIWI"}}});
  const auto result = matcher.Match(crop, "KIWI");
  CHECK(test, result.state == lol_assistant::vision::IconMatchState::Matched);
  CHECK(test, result.augment_id == "manifest-template");
}

}  // namespace

int main(const int argc, const char* const argv[]) {
  TestSameImage();
  TestResizeAndBrightnessRobustness();
  TestDifferentImageRejected();
  TestTop2Ambiguity();
  TestModeFilteringAndTiedIcons();
  TestFusionPolicy();
  TestNoTemplates();
  if (argc > 1 && (argc - 1) % 2 == 0) {
    for (int index = 1; index < argc; index += 2) {
      TestWicManifestSelfMatch(argv[index], argv[index + 1]);
    }
  } else if (argc != 1) {
    std::cerr << "usage: icon_matcher_test "
                 "[template.png expected_dhash_hex]...\n";
    return 2;
  }
  std::cout << "icon matcher checks=" << g_checks
            << " failures=" << g_failures << '\n';
  return g_failures == 0 ? 0 : 1;
}
