#include <windows.h>
#include <winrt/base.h>

#include <cstddef>
#include <cstdint>
#include <iostream>

#include "lol_assistant/vision/ocr.h"

namespace {

class ThrowingWindowsOcr final
    : public lol_assistant::vision::WindowsMediaOcrTitleRecognizer {
 protected:
  [[nodiscard]] lol_assistant::vision::OcrTextResult RecognizeCore(
      const lol_assistant::detector::OwningBgraCrop&) const override {
    throw winrt::hresult_error{E_FAIL, L"injected OCR runtime failure"};
  }
};

}  // namespace

int main() {
  const ThrowingWindowsOcr throwing_ocr;
  const auto caught = throwing_ocr.Recognize({});
  if (caught.state !=
          lol_assistant::vision::OcrResultState::RecognitionFailed ||
      caught.reason.find("recognition_failed:0x80004005") != 0U) {
    std::cerr << "[FAIL] noexcept OCR exception boundary did not translate "
                 "the injected HRESULT\n";
    return 1;
  }

  const lol_assistant::vision::WindowsMediaOcrTitleRecognizer ocr;
  const auto status = ocr.Probe();
  if (!status.available()) {
    std::cout << "[SKIP] " << status.backend << ' ' << status.reason << '\n';
    return 77;
  }

  lol_assistant::detector::OwningBgraCrop crop;
  crop.width = 256U;
  crop.height = 64U;
  crop.stride = crop.width * 4U;
  crop.pixels.resize(static_cast<std::size_t>(crop.stride) * crop.height, 255U);
  const auto result = ocr.Recognize(crop);
  if (!result.ok()) {
    std::cerr << "[FAIL] backend=" << result.backend
              << " reason=" << result.reason << '\n';
    return 1;
  }
  if (result.backend != lol_assistant::vision::kWindowsMediaOcrZhCnBackend ||
      result.ocr_confidence.has_value() ||
      result.line_candidates.size() >
          lol_assistant::vision::kMaximumOcrLineCandidates) {
    std::cerr << "[FAIL] backend identity/confidence contract violated\n";
    return 1;
  }
  std::cout << "OCR backend smoke passed; blank synthetic image recognition "
               "does not measure title accuracy. raw_text_size="
            << result.raw_text.size() << '\n';
  return 0;
}
