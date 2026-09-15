#include "lol_assistant/vision/title_preprocessor.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <set>
#include <string>
#include <string_view>
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

[[nodiscard]] lol_assistant::detector::OwningBgraCrop MakeGrayBgraCrop(
    const std::uint32_t width, const std::uint32_t height,
    const std::vector<std::uint8_t>& gray_values) {
  lol_assistant::detector::OwningBgraCrop crop;
  crop.width = width;
  crop.height = height;
  crop.stride = width * 4U;
  crop.pixels.resize(static_cast<std::size_t>(crop.stride) * height);
  for (std::size_t index = 0U; index < gray_values.size(); ++index) {
    crop.pixels[index * 4U + 0U] = gray_values[index];
    crop.pixels[index * 4U + 1U] = gray_values[index];
    crop.pixels[index * 4U + 2U] = gray_values[index];
    crop.pixels[index * 4U + 3U] = 255U;
  }
  return crop;
}

[[nodiscard]] const lol_assistant::vision::TitlePreprocessVariant* FindVariant(
    const lol_assistant::vision::TitlePreprocessEnumerationResult& result,
    const std::string_view id) {
  const auto found = std::find_if(
      result.variants.begin(), result.variants.end(),
      [&](const lol_assistant::vision::TitlePreprocessVariant& variant) {
        return variant.id == id;
      });
  return found == result.variants.end() ? nullptr : &*found;
}

void TestNeutralDefaultAndGrayscale() {
  constexpr std::string_view test = "neutral default and grayscale";
  const auto defaults =
      lol_assistant::vision::TitlePreprocessor::DefaultParameters();
  CHECK(test, !defaults.contrast_stretch);
  CHECK(test, defaults.threshold_mode ==
                  lol_assistant::vision::TitleThresholdMode::None);
  CHECK(test, defaults.scale == 1U);

  const auto crop = MakeGrayBgraCrop(3U, 2U, {0U, 10U, 64U, 127U, 200U, 255U});
  const auto result = lol_assistant::vision::TitlePreprocessor::Process(
      crop, defaults);
  CHECK(test, result.ok());
  CHECK(test, result.reason == "ok");
  CHECK(test, result.value.has_value() && result.value->id == "gray_1x");
  CHECK(test, result.value.has_value() && result.value->image.width == 3U);
  CHECK(test, result.value.has_value() && result.value->image.height == 2U);
  CHECK(test, (result.value.has_value() &&
               result.value->image.pixels ==
                   std::vector<std::uint8_t>{0U, 10U, 64U, 127U, 200U,
                                             255U}));
  CHECK(test, result.value.has_value() &&
                  !result.value->parameters.resolved_threshold.has_value());
}

void TestContrastThresholdAndScaling() {
  constexpr std::string_view test = "contrast threshold and scaling";
  const auto crop = MakeGrayBgraCrop(2U, 1U, {10U, 100U});
  lol_assistant::vision::TitlePreprocessParameters parameters;
  parameters.contrast_stretch = true;
  parameters.threshold_mode = lol_assistant::vision::TitleThresholdMode::Otsu;
  parameters.scale = 3U;
  const auto result = lol_assistant::vision::TitlePreprocessor::Process(
      crop, parameters);
  CHECK(test, result.ok());
  CHECK(test, result.value.has_value() &&
                  result.value->id == "contrast_otsu_3x");
  CHECK(test, result.value.has_value() &&
                  result.value->parameters.contrast_black_point == 10U);
  CHECK(test, result.value.has_value() &&
                  result.value->parameters.contrast_white_point == 100U);
  CHECK(test, result.value.has_value() &&
                  result.value->parameters.resolved_threshold == 0U);
  CHECK(test, result.value.has_value() && result.value->image.width == 6U);
  CHECK(test, result.value.has_value() && result.value->image.height == 3U);
  CHECK(test, (result.value.has_value() &&
               result.value->image.pixels ==
                   std::vector<std::uint8_t>{
                       0U, 0U, 0U, 255U, 255U, 255U,
                       0U, 0U, 0U, 255U, 255U, 255U,
                       0U, 0U, 0U, 255U, 255U, 255U}));

  parameters.contrast_stretch = false;
  parameters.threshold_mode = lol_assistant::vision::TitleThresholdMode::Fixed;
  parameters.fixed_threshold = 64U;
  parameters.scale = 1U;
  const auto fixed = lol_assistant::vision::TitlePreprocessor::Process(
      MakeGrayBgraCrop(3U, 1U, {63U, 64U, 65U}), parameters);
  CHECK(test, fixed.ok());
  CHECK(test, fixed.value.has_value() && fixed.value->id == "fixed64_1x");
  CHECK(test, fixed.value.has_value() &&
                  fixed.value->parameters.resolved_threshold == 64U);
  CHECK(test, (fixed.value.has_value() &&
               fixed.value->image.pixels ==
                   std::vector<std::uint8_t>{0U, 0U, 255U}));
}

void TestEnumerationAndDeterminism() {
  constexpr std::string_view test = "variant enumeration determinism";
  const auto crop = MakeGrayBgraCrop(
      4U, 2U, {0U, 32U, 96U, 255U, 255U, 96U, 32U, 0U});
  const auto first =
      lol_assistant::vision::TitlePreprocessor::EnumerateVariants(crop);
  const auto second =
      lol_assistant::vision::TitlePreprocessor::EnumerateVariants(crop);
  CHECK(test, first.ok());
  CHECK(test, first.reason == "ok");
  CHECK(test, first.variants.size() == 18U);
  CHECK(test, first.variants == second.variants);
  CHECK(test, !first.variants.empty() && first.variants.front().id == "gray_1x");
  CHECK(test, !first.variants.empty() &&
                  first.variants.front().parameters.requested ==
                      lol_assistant::vision::TitlePreprocessor::
                          DefaultParameters());

  std::set<std::string> ids;
  for (const auto& variant : first.variants) {
    CHECK(test, variant.IsValid());
    ids.insert(variant.id);
    CHECK(test, variant.image.width ==
                    crop.width * variant.parameters.requested.scale);
    CHECK(test, variant.image.height ==
                    crop.height * variant.parameters.requested.scale);
  }
  CHECK(test, ids.size() == first.variants.size());
  CHECK(test, FindVariant(first, "gray_2x") != nullptr);
  CHECK(test, FindVariant(first, "contrast_gray_3x") != nullptr);
  CHECK(test, FindVariant(first, "fixed128_2x") != nullptr);
  CHECK(test, FindVariant(first, "contrast_fixed128_3x") != nullptr);
  const auto* otsu = FindVariant(first, "otsu_1x");
  CHECK(test, otsu != nullptr);
  CHECK(test, otsu != nullptr &&
                  otsu->parameters.resolved_threshold.has_value());
  CHECK(test, FindVariant(first, "contrast_otsu_2x") != nullptr);
}

void TestInvalidInputs() {
  constexpr std::string_view test = "preprocessor invalid inputs";
  lol_assistant::detector::OwningBgraCrop invalid;
  const auto invalid_crop = lol_assistant::vision::TitlePreprocessor::Process(
      invalid, lol_assistant::vision::TitlePreprocessor::DefaultParameters());
  CHECK(test, !invalid_crop.ok());
  CHECK(test, invalid_crop.reason == "invalid_title_crop");
  CHECK(test,
        !lol_assistant::vision::TitlePreprocessor::EnumerateVariants(invalid)
             .ok());

  auto parameters =
      lol_assistant::vision::TitlePreprocessor::DefaultParameters();
  parameters.scale = 4U;
  const auto invalid_parameters =
      lol_assistant::vision::TitlePreprocessor::Process(
          MakeGrayBgraCrop(1U, 1U, {100U}), parameters);
  CHECK(test, !invalid_parameters.ok());
  CHECK(test, invalid_parameters.reason == "invalid_preprocess_parameters");
}

}  // namespace

int main() {
  TestNeutralDefaultAndGrayscale();
  TestContrastThresholdAndScaling();
  TestEnumerationAndDeterminism();
  TestInvalidInputs();
  std::cout << "Preprocess variants are benchmark candidates only; enumeration "
               "order is not an OCR accuracy ranking.\n";
  std::cout << "title_preprocessor checks=" << g_checks
            << " failures=" << g_failures << '\n';
  return g_failures == 0 ? 0 : 1;
}
