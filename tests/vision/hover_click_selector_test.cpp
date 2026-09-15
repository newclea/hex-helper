#include "lol_assistant/vision/hover_click_selector.h"

#include <array>
#include <iostream>
#include <string_view>

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

void TestGlowThenClickSelectsSlot() {
  constexpr std::string_view test = "glow then left-click selects slot";
  lol_assistant::vision::HoverClickSelector selector;
  const std::array<float, 3> idle{80.0F, 82.0F, 79.0F};
  selector.ObserveLuma(idle);
  CHECK(test, !selector.glowing_slot().has_value());
  CHECK(test, !selector.ConfirmClick().has_value());

  const std::array<float, 3> hover{80.0F, 130.0F, 79.0F};
  selector.ObserveLuma(hover);
  CHECK(test, !selector.glowing_slot().has_value());
  selector.ObserveLuma(hover);
  CHECK(test, selector.glowing_slot() == 1U);
  const auto selected = selector.ConfirmClick();
  CHECK(test, selected == 1U);
  CHECK(test, selector.emitted());
  CHECK(test, !selector.ConfirmClick().has_value());
}

void TestClickWithoutGlowDoesNothing() {
  constexpr std::string_view test = "click without glow is ignored";
  lol_assistant::vision::HoverClickSelector selector;
  selector.ObserveLuma({90.0F, 88.0F, 91.0F});
  selector.ObserveLuma({90.0F, 88.0F, 91.0F});
  CHECK(test, !selector.ConfirmClick().has_value());
  CHECK(test, !selector.emitted());
}

void TestGraceKeepsRecentGlow() {
  constexpr std::string_view test = "grace keeps a just-faded glow";
  lol_assistant::vision::HoverClickConfig config;
  config.stable_frames = 2U;
  config.grace_frames = 2U;
  lol_assistant::vision::HoverClickSelector selector{config};
  const std::array<float, 3> hover{140.0F, 80.0F, 82.0F};
  selector.ObserveLuma(hover);
  selector.ObserveLuma(hover);
  CHECK(test, selector.glowing_slot() == 0U);
  selector.ObserveLuma({80.0F, 80.0F, 82.0F});
  CHECK(test, selector.glowing_slot() == 0U);
  CHECK(test, selector.ConfirmClick() == 0U);
}

void TestFlashPicksLeftFromPrismaticClip() {
  constexpr std::string_view test = "prism clip flash selects left";
  const std::array<float, 3> idle{57.6F, 60.8F, 53.5F};
  const std::array<float, 3> flash{87.8F, 45.9F, 39.5F};
  CHECK(test, !lol_assistant::vision::DetectCardFlashSelection(idle, nullptr)
                   .has_value());
  CHECK(test,
        lol_assistant::vision::DetectCardFlashSelection(flash, &idle) == 0U);
}

void TestFlashPicksCenterFromGoldClip() {
  constexpr std::string_view test = "gold clip flash selects center";
  const std::array<float, 3> idle{39.3F, 46.8F, 48.9F};
  const std::array<float, 3> flash{36.6F, 151.9F, 56.1F};
  CHECK(test,
        lol_assistant::vision::DetectCardFlashSelection(flash, &idle) == 1U);
}

void TestHoverIsNotAFlash() {
  constexpr std::string_view test = "hover luma is not a pick flash";
  const std::array<float, 3> hover{55.6F, 60.7F, 53.6F};
  CHECK(test, !lol_assistant::vision::DetectCardFlashSelection(hover, &hover)
                   .has_value());
}

void TestTrackerIgnoresFlashUntilTwoCardsVanish() {
  constexpr std::string_view test = "tracker waits for two cards to vanish";
  lol_assistant::vision::CardPickTracker tracker;
  const std::array<float, 3> idle{0.12F, 0.07F, 0.18F};
  const std::array<float, 3> hover{0.18F, 0.05F, 0.17F};
  const std::array<float, 3> remain{0.09F, 0.46F, 0.44F};
  const std::array<float, 3> fountain{0.86F, 0.64F, 0.58F};
  CHECK(test, !tracker.Observe(idle).has_value());
  CHECK(test, !tracker.Observe(hover).has_value());
  CHECK(test, tracker.Observe(remain) == 0U);
  CHECK(test, tracker.emitted());
  CHECK(test, !tracker.Observe(fountain).has_value());
}

void TestCollapsePicksTheLitCardWhenOthersVanish() {
  constexpr std::string_view test = "collapse keeps the lit card";
  const std::array<float, 3> idle{52.0F, 54.0F, 51.0F};
  const std::array<float, 3> vanish{88.0F, 18.0F, 16.0F};
  CHECK(test, lol_assistant::vision::DetectCardCollapseSelection(vanish, idle) ==
                  0U);
  const std::array<float, 3> hover{55.0F, 61.0F, 53.0F};
  CHECK(test, !lol_assistant::vision::DetectCardCollapseSelection(hover, idle)
                   .has_value());
}

void TestSoleRemainingPicksTheLastLitCard() {
  constexpr std::string_view test = "sole remaining card is the pick";
  const std::array<float, 3> remain{0.09F, 0.46F, 0.44F};
  CHECK(test, lol_assistant::vision::DetectSoleRemainingCard(remain) == 0U);
  const std::array<float, 3> hover{0.12F, 0.07F, 0.18F};
  CHECK(test, !lol_assistant::vision::DetectSoleRemainingCard(hover).has_value());
}

void TestTrackerPicksCenterWhenLeftAndRightVanish() {
  constexpr std::string_view test = "center remains after left and right vanish";
  lol_assistant::vision::CardPickTracker tracker;
  const std::array<float, 3> idle{0.07F, 0.10F, 0.16F};
  const std::array<float, 3> hover{0.08F, 0.12F, 0.17F};
  const std::array<float, 3> remain{0.43F, -0.44F, 0.47F};
  CHECK(test, !tracker.Observe(idle).has_value());
  CHECK(test, !tracker.Observe(hover).has_value());
  CHECK(test, tracker.Observe(remain) == 1U);
}

std::optional<std::size_t> Replay(
    lol_assistant::vision::CardPickTracker &tracker,
    const std::array<float, 3> *frames, const std::size_t count) {
  std::optional<std::size_t> picked;
  for (std::size_t index = 0U; index < count; ++index) {
    const auto slot = tracker.Observe(frames[index]);
    if (slot.has_value()) {
      picked = slot;
    }
  }
  return picked;
}

void TestPrismClipPicksLeftWhenTwoCardsVanish() {
  constexpr std::string_view test = "prism.mp4 alley match picks left";
  // Background-likeness from d:\\Arahat0\\Documents\\棱彩.mp4.
  const std::array<float, 3> frames[] = {
      {0.12F, 0.07F, 0.18F}, {0.11F, 0.07F, 0.17F}, {0.11F, 0.07F, 0.17F},
      {0.22F, 0.04F, -0.01F}, {0.14F, 0.21F, 0.07F}, {0.14F, 0.47F, 0.33F},
      {0.09F, 0.46F, 0.44F}, {0.86F, 0.64F, 0.58F},
  };
  lol_assistant::vision::CardPickTracker tracker;
  CHECK(test, Replay(tracker, frames, 8U) == 0U);
  CHECK(test, tracker.emitted());
}

void TestGoldClipPicksCenterWhenTwoCardsVanish() {
  constexpr std::string_view test = "gold.mp4 alley match picks center";
  // Background-likeness from d:\\Arahat0\\Documents\\黄金.mp4.
  const std::array<float, 3> frames[] = {
      {0.07F, 0.10F, 0.16F}, {0.07F, 0.10F, 0.17F}, {0.07F, 0.10F, 0.17F},
      {0.13F, 0.01F, 0.15F}, {0.34F, 0.02F, 0.18F}, {0.43F, -0.44F, 0.47F},
      {0.58F, 0.48F, 0.66F},
  };
  lol_assistant::vision::CardPickTracker tracker;
  CHECK(test, Replay(tracker, frames, 7U) == 1U);
  CHECK(test, tracker.emitted());
}

void TestPrismStillPicksIfVanishFrameDropped() {
  constexpr std::string_view test = "prism pick survives a dropped vanish frame";
  lol_assistant::vision::CardPickTracker tracker;
  CHECK(test, !tracker.Observe({0.11F, 0.07F, 0.17F}).has_value());
  CHECK(test, !tracker.Observe({0.14F, 0.21F, 0.07F}).has_value());
  CHECK(test, tracker.Observe({0.09F, 0.46F, 0.44F}) == 0U);
}

void TestGoldStillPicksIfVanishFrameDropped() {
  constexpr std::string_view test = "gold pick survives a dropped vanish frame";
  lol_assistant::vision::CardPickTracker tracker;
  CHECK(test, !tracker.Observe({0.07F, 0.10F, 0.17F}).has_value());
  CHECK(test, !tracker.Observe({0.13F, 0.01F, 0.15F}).has_value());
  CHECK(test, tracker.Observe({0.43F, -0.44F, 0.47F}) == 1U);
}

void TestFountainBloomDoesNotReplaceOfferBaseline() {
  constexpr std::string_view test = "fountain bloom does not arm a new offer";
  lol_assistant::vision::CardPickTracker tracker;
  CHECK(test, !tracker.Observe({0.12F, 0.07F, 0.18F}).has_value());
  CHECK(test, !tracker.Observe({0.86F, 0.64F, 0.58F}).has_value());
  CHECK(test, !tracker.emitted());
}

void TestTwoPresentStillArmsOffer() {
  constexpr std::string_view test = "two present cards arm the offer";
  lol_assistant::vision::CardPickTracker tracker;
  CHECK(test, !tracker.Observe({0.10F, 0.11F, 0.32F}).has_value());
  CHECK(test, tracker.offer_seen());
  CHECK(test, tracker.Observe({0.09F, 0.46F, 0.44F}) == 0U);
}

void TestAllGoneUsesRememberedSoleCard() {
  constexpr std::string_view test = "all-gone uses last sole remaining card";
  lol_assistant::vision::CardPickTracker tracker;
  CHECK(test, !tracker.Observe({0.10F, 0.11F, 0.12F}).has_value());
  CHECK(test, !tracker.Observe({0.10F, 0.33F, 0.35F}).has_value());
  CHECK(test, tracker.Observe({0.55F, 0.52F, 0.61F}) == 0U);
  CHECK(test, tracker.emitted());
}

void TestArmOfferAllowsPickWithoutThreePresent() {
  constexpr std::string_view test = "OCR-armed offer can pick without 3 present";
  lol_assistant::vision::CardPickTracker tracker;
  tracker.ArmOffer();
  CHECK(test, tracker.Observe({0.09F, 0.46F, 0.44F}) == 0U);
}

void TestNewOfferClearsSelection() {
  constexpr std::string_view test = "new offer can be selected again";
  lol_assistant::vision::HoverClickSelector selector;
  const std::array<float, 3> hover{80.0F, 80.0F, 140.0F};
  selector.ObserveLuma(hover);
  selector.ObserveLuma(hover);
  CHECK(test, selector.ConfirmClick() == 2U);
  selector.ResetForNewOffer();
  CHECK(test, !selector.emitted());
  CHECK(test, !selector.glowing_slot().has_value());
  selector.ObserveLuma(hover);
  selector.ObserveLuma(hover);
  CHECK(test, selector.ConfirmClick() == 2U);
}

}  // namespace

int main() {
  TestGlowThenClickSelectsSlot();
  TestClickWithoutGlowDoesNothing();
  TestGraceKeepsRecentGlow();
  TestFlashPicksLeftFromPrismaticClip();
  TestFlashPicksCenterFromGoldClip();
  TestHoverIsNotAFlash();
  TestTrackerIgnoresFlashUntilTwoCardsVanish();
  TestTrackerPicksCenterWhenLeftAndRightVanish();
  TestPrismClipPicksLeftWhenTwoCardsVanish();
  TestGoldClipPicksCenterWhenTwoCardsVanish();
  TestPrismStillPicksIfVanishFrameDropped();
  TestGoldStillPicksIfVanishFrameDropped();
  TestFountainBloomDoesNotReplaceOfferBaseline();
  TestCollapsePicksTheLitCardWhenOthersVanish();
  TestSoleRemainingPicksTheLastLitCard();
  TestNewOfferClearsSelection();
  TestTwoPresentStillArmsOffer();
  TestAllGoneUsesRememberedSoleCard();
  TestArmOfferAllowsPickWithoutThreePresent();
  std::cout << "hover click selector checks=" << g_checks
            << " failures=" << g_failures << '\n';
  return g_failures == 0 ? 0 : 1;
}
