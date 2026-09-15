#include "lol_assistant/detector/augment_screen_detector.h"
#include "lol_assistant/detector/roi.h"
#include "lol_assistant/replay/wic_image_codec.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <limits>
#include <stdexcept>
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

constexpr std::array<lol_assistant::detector::PixelRect, 3U>
    kMeasuredClearCards{{
        {506U, 284U, 476U, 785U},
        {1052U, 284U, 476U, 785U},
        {1598U, 284U, 476U, 785U},
    }};
constexpr std::array<lol_assistant::detector::PixelRect, 3U>
    kMeasuredClearTitles{{
        {566U, 631U, 356U, 52U},
        {1112U, 631U, 356U, 52U},
        {1658U, 631U, 356U, 52U},
    }};
constexpr std::array<lol_assistant::detector::PixelRect, 3U>
    kMeasuredClearIcons{{
        {625U, 342U, 240U, 240U},
        {1171U, 342U, 240U, 240U},
        {1717U, 342U, 240U, 240U},
    }};

[[nodiscard]] lol_assistant::detector::NormalizedThreeCardLayout Layout() {
  using lol_assistant::common::NormalizedRoi;
  return {NormalizedRoi{0.10, 0.10, 0.80, 0.80},
          {NormalizedRoi{0.15, 0.20, 0.20, 0.55},
           NormalizedRoi{0.40, 0.20, 0.20, 0.55},
           NormalizedRoi{0.65, 0.20, 0.20, 0.55}}};
}

[[nodiscard]] lol_assistant::common::Frame MakeFrame(const std::uint32_t width,
                                                     const std::uint32_t height,
                                                     const std::uint64_t id) {
  using namespace lol_assistant::common;
  Frame frame;
  frame.source = {FrameSourceKind::Replay, "synthetic-plumbing-only"};
  frame.frame_id = id;
  frame.width = width;
  frame.height = height;
  frame.stride = width * static_cast<std::uint32_t>(Frame::kBytesPerPixel);
  frame.buffer.assign(static_cast<std::size_t>(frame.stride) * height, 8U);
  for (std::size_t offset = 3U; offset < frame.buffer.size();
       offset += Frame::kBytesPerPixel) {
    frame.buffer[offset] = 255U;
  }
  return frame;
}

void PaintSyntheticCards(lol_assistant::common::Frame &frame,
                         const lol_assistant::detector::ThreeCardRois &rois) {
  for (const auto &roi : rois.cards) {
    const auto border_x = std::max(1U, roi.width / 14U);
    const auto border_y = std::max(1U, roi.height / 18U);
    for (std::uint32_t y = 0U; y < roi.height; ++y) {
      for (std::uint32_t x = 0U; x < roi.width; ++x) {
        const bool border = x < border_x || x >= roi.width - border_x ||
                            y < border_y || y >= roi.height - border_y;
        const std::uint8_t value =
            border ? 210U : (((x / 4U + y / 4U) % 2U) == 0U ? 25U : 80U);
        const auto offset = static_cast<std::size_t>(roi.y + y) * frame.stride +
                            static_cast<std::size_t>(roi.x + x) * 4U;
        frame.buffer[offset + 0U] = value;
        frame.buffer[offset + 1U] = value;
        frame.buffer[offset + 2U] = value;
        frame.buffer[offset + 3U] = 255U;
      }
    }
  }
}

void PaintFullFrameChecker(lol_assistant::common::Frame &frame) {
  for (std::uint32_t y = 0U; y < frame.height; ++y) {
    for (std::uint32_t x = 0U; x < frame.width; ++x) {
      const std::uint8_t value = ((x + y) % 2U) == 0U ? 45U : 220U;
      const auto offset = static_cast<std::size_t>(y) * frame.stride +
                          static_cast<std::size_t>(x) * 4U;
      frame.buffer[offset + 0U] = value;
      frame.buffer[offset + 1U] = value;
      frame.buffer[offset + 2U] = value;
      frame.buffer[offset + 3U] = 255U;
    }
  }
}

[[nodiscard]] bool Contains(const lol_assistant::detector::PixelRoi &outer,
                            const lol_assistant::detector::PixelRoi &inner) {
  return inner.x >= outer.x && inner.y >= outer.y &&
         static_cast<std::uint64_t>(inner.x) + inner.width <=
             static_cast<std::uint64_t>(outer.x) + outer.width &&
         static_cast<std::uint64_t>(inner.y) + inner.height <=
             static_cast<std::uint64_t>(outer.y) + outer.height;
}

void TestRoisAcrossResolutions() {
  constexpr std::string_view test = "ROI supported resolutions and sub-ROIs";
  for (const auto [width, height] :
       {std::pair{1920U, 1080U}, std::pair{2560U, 1440U},
        std::pair{2560U, 1600U}}) {
    const auto result =
        lol_assistant::detector::ComputeThreeCardRois(width, height, Layout());
    CHECK(test, result.ok());
    if (!result.ok()) {
      continue;
    }
    CHECK(test, result.reason == "ok");
    CHECK(test, (result.value->resolution ==
                 lol_assistant::detector::FrameResolution{width, height}));
    CHECK(test, result.value->IsValid());
    CHECK(test, result.value->cards[0].x < result.value->cards[1].x);
    CHECK(test, result.value->cards[1].x < result.value->cards[2].x);
    for (std::size_t index = 0U; index < result.value->cards.size(); ++index) {
      const auto &card = result.value->cards[index];
      const auto &title = result.value->title_rects[index];
      const auto &icon = result.value->icon_rects[index];
      CHECK(test, card.IsInside(width, height));
      CHECK(test, title.IsInside(width, height));
      CHECK(test, icon.IsInside(width, height));
      CHECK(test, Contains(card, title));
      CHECK(test, Contains(card, icon));
      CHECK(test, card.primary_ocr_rect.has_value());
      CHECK(test, card.PrimaryCrop() == title.Bounds());
    }
  }

  const auto sixteen_ten =
      lol_assistant::detector::ComputeThreeCardRois(2560U, 1600U, Layout());
  CHECK(test, sixteen_ten.reason != "unsupported_aspect_ratio");
  CHECK(test, sixteen_ten.ok());
  if (sixteen_ten.ok()) {
    for (std::size_t slot = 0U; slot < kMeasuredClearCards.size(); ++slot) {
      CHECK(test, sixteen_ten.value->cards[slot].Bounds() ==
                      kMeasuredClearCards[slot]);
      CHECK(test, sixteen_ten.value->title_rects[slot].Bounds() ==
                      kMeasuredClearTitles[slot]);
      CHECK(test, sixteen_ten.value->icon_rects[slot].Bounds() ==
                      kMeasuredClearIcons[slot]);
    }
  }
  CHECK(test, lol_assistant::detector::IsSupported16By9(2560U, 1600U));
  CHECK(test, !lol_assistant::detector::IsExact16By9(2560U, 1600U));
  CHECK(test, lol_assistant::detector::IsExact16By9(1920U, 1080U));
  CHECK(test,
        lol_assistant::detector::IsSupportedThreeCardAspectRatio(2560U, 1600U));

  for (const auto [width, height] :
       {std::pair{1024U, 768U}, std::pair{3440U, 1440U},
        std::pair{1600U, 2560U}}) {
    const auto result =
        lol_assistant::detector::ComputeThreeCardRois(width, height, Layout());
    CHECK(test, !result.ok());
    CHECK(test, result.reason == "unsupported_aspect_ratio");
  }

  auto invalid = Layout();
  invalid.card_regions[2] = {0.90, 0.20, 0.20, 0.55};
  CHECK(test,
        !lol_assistant::detector::ComputeThreeCardRois(1920U, 1080U, invalid)
             .ok());
  invalid = Layout();
  invalid.title_region_within_card = {0.90, 0.0, 0.20, 0.20};
  CHECK(test,
        !lol_assistant::detector::ComputeThreeCardRois(1920U, 1080U, invalid)
             .ok());
}

void TestRoiCandidatesAcrossCaptureGeometries() {
  using lol_assistant::detector::PixelRect;
  constexpr std::string_view test = "ROI ordered capture-geometry candidates";
  constexpr std::array<PixelRect, 3U> desktop_cards{{
      {570U, 306U, 437U, 721U},
      {1071U, 306U, 437U, 721U},
      {1573U, 306U, 437U, 721U},
  }};
  constexpr std::array<PixelRect, 3U> desktop_titles{{
      {625U, 624U, 327U, 48U},
      {1126U, 624U, 327U, 48U},
      {1628U, 624U, 327U, 48U},
  }};
  constexpr std::array<PixelRect, 3U> desktop_icons{{
      {679U, 359U, 221U, 221U},
      {1180U, 359U, 221U, 221U},
      {1682U, 359U, 221U, 221U},
  }};
  constexpr std::array<PixelRect, 3U> client_cards{{
      {555U, 274U, 441U, 720U},
      {1055U, 274U, 444U, 720U},
      {1562U, 274U, 440U, 720U},
  }};
  constexpr std::array<PixelRect, 3U> client_titles{{
      {610U, 592U, 331U, 48U},
      {1110U, 592U, 334U, 48U},
      {1617U, 592U, 330U, 48U},
  }};
  constexpr std::array<PixelRect, 3U> client_icons{{
      {665U, 327U, 223U, 221U},
      {1166U, 327U, 224U, 221U},
      {1672U, 327U, 222U, 221U},
  }};

  const auto desktop = lol_assistant::detector::ComputeThreeCardRoiCandidates(
      2560U, 1440U, Layout());
  CHECK(test, desktop.ok());
  CHECK(test, desktop.reason == "ok");
  CHECK(test, desktop.values.size() == 2U);
  if (desktop.values.size() == 2U) {
    CHECK(test, (desktop.values[0].cards[0].Bounds() ==
                 PixelRect{506U, 256U, 476U, 706U}));
    for (std::size_t slot = 0U; slot < desktop_cards.size(); ++slot) {
      CHECK(test,
            desktop.values[1].cards[slot].Bounds() == desktop_cards[slot]);
      CHECK(test, desktop.values[1].title_rects[slot].Bounds() ==
                      desktop_titles[slot]);
      CHECK(test,
            desktop.values[1].icon_rects[slot].Bounds() == desktop_icons[slot]);
    }
    for (const auto &candidate : desktop.values) {
      CHECK(test,
            lol_assistant::detector::OffsetThreeCardRois(candidate, -256, -144)
                .has_value());
      CHECK(test,
            lol_assistant::detector::OffsetThreeCardRois(candidate, 256, 144)
                .has_value());
      CHECK(test,
            !lol_assistant::detector::OffsetThreeCardRois(candidate, -257, 0)
                 .has_value());
      CHECK(test,
            !lol_assistant::detector::OffsetThreeCardRois(candidate, 0, 145)
                 .has_value());
    }
  }

  const auto client = lol_assistant::detector::ComputeThreeCardRoiCandidates(
      2538U, 1487U, Layout());
  CHECK(test, client.ok());
  CHECK(test, client.values.size() == 2U);
  if (client.values.size() == 2U) {
    CHECK(test, (client.values[0].cards[0].Bounds() ==
                 PixelRect{501U, 264U, 473U, 730U}));
    for (std::size_t slot = 0U; slot < client_cards.size(); ++slot) {
      CHECK(test, client.values[1].cards[slot].Bounds() == client_cards[slot]);
      CHECK(test,
            client.values[1].title_rects[slot].Bounds() == client_titles[slot]);
      CHECK(test,
            client.values[1].icon_rects[slot].Bounds() == client_icons[slot]);
    }
    for (const auto &candidate : client.values) {
      CHECK(test,
            lol_assistant::detector::OffsetThreeCardRois(candidate, -253, -148)
                .has_value());
      CHECK(test,
            lol_assistant::detector::OffsetThreeCardRois(candidate, 253, 148)
                .has_value());
      CHECK(test,
            !lol_assistant::detector::OffsetThreeCardRois(candidate, 254, 0)
                 .has_value());
      CHECK(test,
            !lol_assistant::detector::OffsetThreeCardRois(candidate, 0, -149)
                 .has_value());
    }
  }

  const auto sixteen_ten =
      lol_assistant::detector::ComputeThreeCardRoiCandidates(2560U, 1600U,
                                                             Layout());
  CHECK(test, sixteen_ten.values.size() == 1U);

  auto explicit_layout = Layout();
  explicit_layout.card_regions[0].x += 0.000001;
  const auto explicit_result =
      lol_assistant::detector::ComputeThreeCardRoiCandidates(2560U, 1440U,
                                                             explicit_layout);
  CHECK(test, explicit_result.values.size() == 1U);

  const auto unsupported =
      lol_assistant::detector::ComputeThreeCardRoiCandidates(3440U, 1440U,
                                                             Layout());
  CHECK(test, !unsupported.ok());
  CHECK(test, unsupported.reason == "unsupported_aspect_ratio");
}

void TestRoundingOffsetsAndSerialization() {
  constexpr std::string_view test = "ROI rounding offsets and serialization";
  const auto result =
      lol_assistant::detector::ComputeThreeCardRois(1366U, 768U, Layout());
  CHECK(test, result.ok());
  if (!result.ok()) {
    return;
  }
  CHECK(test, (result.value->offer_region.Bounds() ==
               lol_assistant::detector::PixelRect{136U, 76U, 1094U, 616U}));
  CHECK(test, (result.value->cards[0].Bounds() ==
               lol_assistant::detector::PixelRect{269U, 136U, 255U, 378U}));

  const auto shifted =
      lol_assistant::detector::OffsetThreeCardRois(*result.value, -17, 11);
  CHECK(test, shifted.has_value());
  CHECK(test, shifted.has_value() &&
                  shifted->cards[1].x + 17U == result.value->cards[1].x);
  CHECK(test, shifted.has_value() &&
                  shifted->cards[1].y == result.value->cards[1].y + 11U);
  CHECK(test, shifted.has_value() && shifted->IsValid());
  CHECK(test, !lol_assistant::detector::OffsetThreeCardRois(
                   *result.value, std::numeric_limits<std::int32_t>::max(), 0)
                   .has_value());
  CHECK(test, !lol_assistant::detector::OffsetThreeCardRois(
                   *result.value, std::numeric_limits<std::int32_t>::min(), 0)
                   .has_value());

  auto calibration = result.value->Calibration();
  CHECK(test, calibration.IsValid());
  const auto first =
      lol_assistant::detector::SerializeRoiCalibration(calibration);
  const auto second =
      lol_assistant::detector::SerializeRoiCalibration(calibration);
  CHECK(test, first.has_value());
  CHECK(test, first == second);
  CHECK(test,
        first.has_value() &&
            first->find("\"resolution\":{\"width\":1366,\"height\":768}") !=
                std::string::npos);
  CHECK(test, first.has_value() &&
                  first->find("\"ui_scale\":null") != std::string::npos);
  CHECK(test, first.has_value() &&
                  first->find("\"card_rect\":[") != std::string::npos);
  CHECK(test, first.has_value() &&
                  first->find("\"title_rect\":[") != std::string::npos);
  CHECK(test, first.has_value() &&
                  first->find("\"icon_rect\":[") != std::string::npos);

  calibration.ui_scale = 1.25;
  const auto scaled =
      lol_assistant::detector::SerializeRoiCalibration(calibration);
  CHECK(test, scaled.has_value() &&
                  scaled->find("\"ui_scale\":1.25") != std::string::npos);
  calibration.ui_scale = 0.0;
  CHECK(test, !calibration.IsValid());
  CHECK(test, !lol_assistant::detector::SerializeRoiCalibration(calibration)
                   .has_value());
}

void TestOwningCrop() {
  constexpr std::string_view test = "BGRA title/raw owning crop and bounds";
  auto frame = MakeFrame(4U, 2U, 1U);
  frame.stride = 20U;
  frame.buffer.resize(40U);
  for (std::size_t index = 0U; index < frame.buffer.size(); ++index) {
    frame.buffer[index] = static_cast<std::uint8_t>(index);
  }
  const auto cropped =
      lol_assistant::detector::CropBgraOwning(frame, {1U, 0U, 2U, 2U});
  CHECK(test, cropped.ok());
  CHECK(test, cropped.value.has_value() && cropped.value->stride == 8U);
  CHECK(test, cropped.value.has_value() && cropped.value->pixels.size() == 16U);
  CHECK(test, cropped.value.has_value() && cropped.value->pixels[0] == 4U);
  CHECK(test, cropped.value.has_value() && cropped.value->pixels[8] == 24U);
  CHECK(test,
        !lol_assistant::detector::CropBgraOwning(frame, {3U, 0U, 2U, 1U}).ok());

  auto title_frame = MakeFrame(320U, 180U, 2U);
  const auto rois = lol_assistant::detector::ComputeThreeCardRois(
      title_frame.width, title_frame.height, Layout());
  CHECK(test, rois.ok());
  if (!rois.ok()) {
    return;
  }
  const auto primary = lol_assistant::detector::CropBgraOwning(
      title_frame, rois.value->cards[0]);
  const auto raw = lol_assistant::detector::CropRawBgraOwning(
      title_frame, rois.value->cards[0]);
  CHECK(test, primary.ok());
  CHECK(test, raw.ok());
  CHECK(test, primary.value.has_value() &&
                  primary.value->width == rois.value->title_rects[0].width);
  CHECK(test, primary.value.has_value() &&
                  primary.value->height == rois.value->title_rects[0].height);
  CHECK(test, raw.value.has_value() &&
                  raw.value->width == rois.value->cards[0].width);
  CHECK(test, raw.value.has_value() &&
                  raw.value->height == rois.value->cards[0].height);
}

void TestSixteenTenFeatureGatingAndLocalSearch() {
  constexpr std::string_view test = "16:10 feature gates and local ROI search";
  lol_assistant::detector::AugmentScreenDetectorConfig config;
  config.layout = Layout();
  config.ui_scale = 1.25;
  config.sample_step = 1U;
  lol_assistant::detector::AugmentScreenDetector detector{config};

  auto frame = MakeFrame(256U, 160U, 1U);
  const auto seed = lol_assistant::detector::ComputeThreeCardRois(
      frame.width, frame.height, Layout());
  CHECK(test, seed.ok());
  PaintSyntheticCards(frame, *seed.value);
  const auto detected = detector.Detect(frame);
  CHECK(test, detected.visible);
  CHECK(test, detected.reason == "visible");
  CHECK(test, detected.rois.has_value());
  CHECK(test, detected.rois.has_value() && detected.rois->ui_scale == 1.25);
  CHECK(test, detected.metrics.evaluated_candidates > 1U);
  for (std::size_t index = 0U; index < lol_assistant::common::kAugmentCardCount;
       ++index) {
    CHECK(test, detected.metrics.mean_luma[index] >= config.minimum_card_luma);
    CHECK(test,
          detected.metrics.edge_density[index] >= config.minimum_edge_density);
    CHECK(test, detected.metrics.surround_luma_contrast[index] >=
                    config.minimum_surround_luma_contrast);
    CHECK(test, detected.metrics.bright_border_density[index] >=
                    config.minimum_bright_border_density);
    CHECK(test, detected.metrics.border_luma_contrast[index] >=
                    config.minimum_border_luma_contrast);
  }

  auto blank = MakeFrame(256U, 160U, 2U);
  const auto blank_result = detector.Detect(blank);
  CHECK(test, !blank_result.visible);
  CHECK(test, blank_result.reason != "unsupported_aspect_ratio");

  auto arbitrary = MakeFrame(256U, 160U, 3U);
  PaintFullFrameChecker(arbitrary);
  const auto arbitrary_result = detector.Detect(arbitrary);
  CHECK(test, !arbitrary_result.visible);
  CHECK(test, arbitrary_result.reason == "insufficient_card_surround_contrast");

  auto shifted_frame = MakeFrame(320U, 180U, 4U);
  const auto shifted_seed = lol_assistant::detector::ComputeThreeCardRois(
      shifted_frame.width, shifted_frame.height, Layout());
  CHECK(test, shifted_seed.ok());
  const auto painted =
      lol_assistant::detector::OffsetThreeCardRois(*shifted_seed.value, 3, 4);
  CHECK(test, painted.has_value());
  PaintSyntheticCards(shifted_frame, *painted);
  const auto shifted_result = detector.Detect(shifted_frame);
  CHECK(test, shifted_result.visible);
  CHECK(test, shifted_result.rois.has_value());
  CHECK(test, shifted_result.metrics.selected_offset_x == 3);
  CHECK(test, shifted_result.metrics.selected_offset_y == 4);
  CHECK(test, shifted_result.rois.has_value() &&
                  shifted_result.rois->cards[0].x == painted->cards[0].x);

  auto excessive_search = config;
  excessive_search.roi_search.offset_step_ratio = 0.000001;
  CHECK(test, !excessive_search.IsValid());

  auto unsafe_sampling = config;
  unsafe_sampling.sample_step = 1U << 30U;
  CHECK(test, !unsafe_sampling.IsValid());
  bool rejected_unsafe_sampling = false;
  try {
    static_cast<void>(
        lol_assistant::detector::AugmentScreenDetector{unsafe_sampling});
  } catch (const std::invalid_argument &) {
    rejected_unsafe_sampling = true;
  }
  CHECK(test, rejected_unsafe_sampling);
}

void TestOrderedRoiCandidateSelection() {
  constexpr std::string_view test = "ordered ROI seed selection";
  const auto candidates =
      lol_assistant::detector::ComputeThreeCardRoiCandidates(2560U, 1440U,
                                                             Layout());
  CHECK(test, candidates.values.size() == 2U);
  if (candidates.values.size() != 2U) {
    return;
  }

  lol_assistant::detector::AugmentScreenDetectorConfig config;
  config.layout = Layout();
  config.roi_search.enabled = false;
  config.sample_step = 2U;
  lol_assistant::detector::AugmentScreenDetector detector{config};

  auto primary_frame = MakeFrame(2560U, 1440U, 80U);
  PaintSyntheticCards(primary_frame, candidates.values[0]);
  const auto primary = detector.Detect(primary_frame);
  CHECK(test, primary.visible);
  CHECK(test, primary.rois.has_value());
  CHECK(test,
        primary.rois.has_value() && primary.rois->cards[0].Bounds() ==
                                        candidates.values[0].cards[0].Bounds());

  auto compact_frame = MakeFrame(2560U, 1440U, 81U);
  PaintSyntheticCards(compact_frame, candidates.values[1]);
  const auto compact = detector.Detect(compact_frame);
  CHECK(test, compact.visible);
  CHECK(test, compact.rois.has_value());
  CHECK(test,
        compact.rois.has_value() && compact.rois->cards[0].Bounds() ==
                                        candidates.values[1].cards[0].Bounds());
}

void TestSyntheticDetectorPlumbingAndStability() {
  constexpr std::string_view test = "synthetic detector plumbing";
  lol_assistant::detector::AugmentScreenDetectorConfig config;
  config.layout = Layout();
  config.sample_step = 1U;
  lol_assistant::detector::AugmentScreenDetector detector{config};

  auto frame = MakeFrame(320U, 180U, 1U);
  const auto rois = lol_assistant::detector::ComputeThreeCardRois(
      frame.width, frame.height, Layout());
  CHECK(test, rois.ok());
  PaintSyntheticCards(frame, *rois.value);
  const auto detected = detector.Detect(frame);
  CHECK(test, detected.visible);
  CHECK(test, detected.reason == "visible");
  CHECK(test, detected.confidence >= config.visible_confidence_threshold);

  lol_assistant::detector::ConsecutiveFrameConfirmer confirmer{{3U, 0.62F}};
  const auto first = confirmer.Observe(detected);
  CHECK(test, !first.visible);
  // The live WGC source can drop many producer frames between throttled
  // detector observations. Strictly increasing ids still represent three
  // consecutive observations by this consumer.
  frame.frame_id = 9U;
  const auto second = confirmer.Observe(detector.Detect(frame));
  CHECK(test, !second.visible);
  frame.frame_id = 42U;
  const auto third = confirmer.Observe(detector.Detect(frame));
  CHECK(test, third.visible);
  CHECK(test, third.reason == "stable_visible");

  const auto duplicate = confirmer.Observe(detector.Detect(frame));
  CHECK(test, !duplicate.visible);
  CHECK(test, duplicate.reason == "non_contiguous_frame");

  auto blank = MakeFrame(320U, 180U, 43U);
  const auto absent = detector.Detect(blank);
  CHECK(test, !absent.visible);
  CHECK(test, absent.reason == "insufficient_luma" ||
                  absent.reason == "insufficient_edges");
}

namespace fs = std::filesystem;

[[nodiscard]] fs::path FindSourceRoot() {
  std::array<fs::path, 2U> seeds{fs::current_path(),
                                 fs::absolute(fs::path{__FILE__})};
  for (auto seed : seeds) {
    if (!fs::is_directory(seed)) {
      seed = seed.parent_path();
    }
    for (std::size_t depth = 0U; depth < 12U && !seed.empty(); ++depth) {
      if (fs::is_regular_file(seed / "CMakeLists.txt") &&
          fs::is_directory(seed / "data/dataset/augment_offers/real")) {
        return seed;
      }
      const auto parent = seed.parent_path();
      if (parent == seed) {
        break;
      }
      seed = parent;
    }
  }
  throw std::runtime_error("cannot locate detector test source root");
}

[[nodiscard]] lol_assistant::common::Frame
CropFrame(const lol_assistant::common::Frame &source,
          const lol_assistant::detector::PixelRect rect,
          const std::uint64_t frame_id) {
  if (!source.IsValid() || !rect.IsInside(source.width, source.height)) {
    throw std::runtime_error("invalid detector fixture crop");
  }
  lol_assistant::common::Frame result;
  result.source = {lol_assistant::common::FrameSourceKind::Replay,
                   "detector-real-fixture-crop"};
  result.frame_id = frame_id;
  result.width = rect.width;
  result.height = rect.height;
  result.stride =
      rect.width *
      static_cast<std::uint32_t>(lol_assistant::common::Frame::kBytesPerPixel);
  result.buffer.resize(static_cast<std::size_t>(result.stride) * result.height);
  for (std::uint32_t y = 0U; y < rect.height; ++y) {
    const auto *source_begin =
        source.buffer.data() +
        static_cast<std::size_t>(rect.y + y) * source.stride +
        static_cast<std::size_t>(rect.x) *
            lol_assistant::common::Frame::kBytesPerPixel;
    std::copy_n(source_begin, result.stride,
                result.buffer.data() +
                    static_cast<std::size_t>(y) * result.stride);
  }
  return result;
}

[[nodiscard]] double
IntersectionOverUnion(const lol_assistant::detector::PixelRect &left,
                      const lol_assistant::detector::PixelRect &right) {
  const auto intersection_left = std::max(left.x, right.x);
  const auto intersection_top = std::max(left.y, right.y);
  const auto intersection_right =
      std::min(static_cast<std::uint64_t>(left.x) + left.width,
               static_cast<std::uint64_t>(right.x) + right.width);
  const auto intersection_bottom =
      std::min(static_cast<std::uint64_t>(left.y) + left.height,
               static_cast<std::uint64_t>(right.y) + right.height);
  if (intersection_right <= intersection_left ||
      intersection_bottom <= intersection_top) {
    return 0.0;
  }
  const auto intersection = (intersection_right - intersection_left) *
                            (intersection_bottom - intersection_top);
  const auto left_area = static_cast<std::uint64_t>(left.width) * left.height;
  const auto right_area =
      static_cast<std::uint64_t>(right.width) * right.height;
  return static_cast<double>(intersection) /
         static_cast<double>(left_area + right_area - intersection);
}

void PrintDetection(const std::string_view id,
                    const lol_assistant::detector::DetectorResult &result) {
  std::cout << "REAL_DETECT id=" << id << " visible=" << result.visible
            << " confidence=" << result.confidence
            << " reason=" << result.reason
            << " offset=" << result.metrics.selected_offset_x << ','
            << result.metrics.selected_offset_y << " bright_border=";
  for (std::size_t index = 0U;
       index < result.metrics.bright_border_density.size(); ++index) {
    if (index != 0U) {
      std::cout << ',';
    }
    std::cout << result.metrics.bright_border_density[index];
  }
  std::cout << " border_contrast=";
  for (std::size_t index = 0U;
       index < result.metrics.border_luma_contrast.size(); ++index) {
    if (index != 0U) {
      std::cout << ',';
    }
    std::cout << result.metrics.border_luma_contrast[index];
  }
  std::cout << " luma=";
  for (const auto value : result.metrics.mean_luma) {
    std::cout << value << ',';
  }
  std::cout << " edges=";
  for (const auto value : result.metrics.edge_density) {
    std::cout << value << ',';
  }
  std::cout << " surround=";
  for (const auto value : result.metrics.surround_luma_contrast) {
    std::cout << value << ',';
  }
  std::cout << " consistency=" << result.metrics.three_column_consistency
            << '\n';
}

void TestRealWgcAndPreviewFixtures() {
  constexpr std::string_view test = "real WGC detector and ROI regression";
  const auto root = FindSourceRoot();
  lol_assistant::detector::AugmentScreenDetectorConfig config;
  config.layout = Layout();
  lol_assistant::detector::AugmentScreenDetector detector{config};

  const std::array<std::string_view, 6U> wgc_samples{{
      "sample_1787673873611452_f2_p30896_n0_0309c4711657f988",
      "sample_1787673874882312_f69_p30896_n1_77c5f72fbff68bea",
      "sample_1787673876034130_f134_p30896_n2_7d20cfaed875701d",
      "sample_1787673877200290_f199_p30896_n3_47d9c66595f20b78",
      "sample_1787673880393715_f382_p30896_n4_d56184b2891fa1ac",
      "sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6",
  }};
  for (std::size_t index = 0U; index < wgc_samples.size(); ++index) {
    const auto path = root / "data/dataset/augment_offers/real" /
                      wgc_samples[index] / "RAW.png";
    CHECK(test, fs::is_regular_file(path));
    const auto frame = lol_assistant::replay::WicImageCodec::Decode(
        path,
        {lol_assistant::common::FrameSourceKind::Replay,
         std::string{wgc_samples[index]}},
        index + 1U);
    const auto result = detector.Detect(frame);
    PrintDetection(wgc_samples[index], result);
    if (index + 1U == wgc_samples.size()) {
      CHECK(test, result.visible);
      CHECK(test, result.reason == "visible");
      CHECK(test, result.rois.has_value());
      if (result.rois.has_value()) {
        for (std::size_t slot = 0U; slot < kMeasuredClearCards.size(); ++slot) {
          const auto card_iou = IntersectionOverUnion(
              result.rois->cards[slot].Bounds(), kMeasuredClearCards[slot]);
          const auto title_iou =
              IntersectionOverUnion(result.rois->title_rects[slot].Bounds(),
                                    kMeasuredClearTitles[slot]);
          const auto icon_rect = result.rois->icon_rects[slot].Bounds();
          const auto icon_iou =
              IntersectionOverUnion(icon_rect, kMeasuredClearIcons[slot]);
          std::cout << "REAL_ROI slot=" << slot << " card_iou=" << card_iou
                    << " title_iou=" << title_iou
                    << " icon_rect=" << icon_rect.x << ',' << icon_rect.y << ','
                    << icon_rect.width << ',' << icon_rect.height
                    << " icon_iou=" << icon_iou << '\n';
          CHECK(test, card_iou >= 0.99);
          CHECK(test, title_iou >= 0.99);
          CHECK(test, icon_iou >= 0.99);
          CHECK(test, icon_rect == kMeasuredClearIcons[slot]);
        }
      }
    } else {
      CHECK(test, !result.visible);
      CHECK(test, result.reason == "insufficient_bright_card_border" ||
                      result.reason == "insufficient_card_border_contrast" ||
                      result.reason == "inconsistent_column_bright_borders" ||
                      result.reason == "inconsistent_column_border_contrast");
    }
  }

  const std::array<std::string_view, 3U> preview_samples{{
      "real-preview-1787670257978-f010",
      "real-preview-1787670257978-f020",
      "real-preview-1787670572085-f0025",
  }};
  for (std::size_t index = 0U; index < preview_samples.size(); ++index) {
    const auto source = lol_assistant::replay::WicImageCodec::Decode(
        root / "data/dataset/augment_offers/real" / preview_samples[index] /
            "RAW.png",
        {lol_assistant::common::FrameSourceKind::Replay,
         std::string{preview_samples[index]}},
        20U + index);
    const auto frame = CropFrame(source, {0U, 0U, 1280U, 720U}, 20U + index);
    const auto result = detector.Detect(frame);
    PrintDetection(preview_samples[index], result);
    CHECK(test, result.visible);
    CHECK(test, result.rois.has_value());
    if (result.rois.has_value()) {
      CHECK(test, (result.rois->cards[0].Bounds() ==
                   lol_assistant::detector::PixelRect{253U, 128U, 238U, 353U}));
      CHECK(test, (result.rois->title_rects[0].Bounds() ==
                   lol_assistant::detector::PixelRect{283U, 284U, 178U, 23U}));
      CHECK(test, (result.rois->icon_rects[0].Bounds() ==
                   lol_assistant::detector::PixelRect{312U, 154U, 121U, 109U}));
    }
  }

  const std::array<std::string_view, 10U> negative_frames{{
      "0015",
      "0060",
      "0085",
      "0090",
      "0095",
      "0105",
      "0110",
      "0115",
      "0120",
      "0130",
  }};
  for (std::size_t index = 0U; index < negative_frames.size(); ++index) {
    const auto path = root /
                      "outputs/phase2_emergency_real/continuous_1787670572085" /
                      ("frame_" + std::string{negative_frames[index]} + ".png");
    CHECK(test, fs::is_regular_file(path));
    const auto source = lol_assistant::replay::WicImageCodec::Decode(
        path,
        {lol_assistant::common::FrameSourceKind::Replay,
         "negative-" + std::string{negative_frames[index]}},
        100U + index);
    const auto frame = CropFrame(source, {0U, 0U, 1280U, 720U}, 100U + index);
    const auto result = detector.Detect(frame);
    PrintDetection("negative", result);
    CHECK(test, !result.visible);
  }
}

} // namespace

int main() {
  TestRoisAcrossResolutions();
  TestRoiCandidatesAcrossCaptureGeometries();
  TestRoundingOffsetsAndSerialization();
  TestOwningCrop();
  TestSixteenTenFeatureGatingAndLocalSearch();
  TestOrderedRoiCandidateSelection();
  TestSyntheticDetectorPlumbingAndStability();
  TestRealWgcAndPreviewFixtures();
  std::cout << "Synthetic images validate geometry/gating plumbing only; they "
               "do not measure LoL ROI or OCR accuracy.\n";
  std::cout << "detector checks=" << g_checks << " failures=" << g_failures
            << '\n';
  return g_failures == 0 ? 0 : 1;
}
