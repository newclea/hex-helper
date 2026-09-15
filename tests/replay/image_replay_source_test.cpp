#include "lol_assistant/replay/image_replay_source.h"
#include "lol_assistant/replay/wic_image_codec.h"

#include <Windows.h>
#include <wincodec.h>
#include <wrl/client.h>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

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

[[nodiscard]] lol_assistant::common::Frame MakeFrame(
    const std::uint32_t width, const std::uint32_t height,
    const std::uint8_t marker) {
  using namespace lol_assistant::common;
  Frame frame;
  frame.source = FrameSource{FrameSourceKind::Replay, "replay-test-generator"};
  frame.width = width;
  frame.height = height;
  frame.stride = width * static_cast<std::uint32_t>(Frame::kBytesPerPixel);
  frame.buffer.resize(static_cast<std::size_t>(frame.stride) * height);
  for (std::uint32_t y = 0U; y < height; ++y) {
    for (std::uint32_t x = 0U; x < width; ++x) {
      const std::size_t offset = static_cast<std::size_t>(y) * frame.stride +
                                 static_cast<std::size_t>(x) * 4U;
      frame.buffer[offset] = static_cast<std::uint8_t>(marker + x);
      frame.buffer[offset + 1U] = static_cast<std::uint8_t>(marker + y);
      frame.buffer[offset + 2U] = static_cast<std::uint8_t>(marker + x + y);
      frame.buffer[offset + 3U] = 255U;
    }
  }
  if (!frame.IsValid()) {
    throw std::runtime_error("Test generated an invalid frame");
  }
  return frame;
}

void WriteText(const std::filesystem::path& path, const std::string& text) {
  std::ofstream output(path, std::ios::binary);
  if (!output) {
    throw std::runtime_error("Unable to create test file: " + path.string());
  }
  output.write(text.data(), static_cast<std::streamsize>(text.size()));
}

[[nodiscard]] bool ThrowsReplayError(const std::function<void()>& action) {
  try {
    action();
  } catch (const lol_assistant::replay::ReplayError&) {
    return true;
  }
  return false;
}

void RequireSucceeded(const HRESULT result, const char* operation) {
  if (FAILED(result)) {
    throw std::runtime_error(std::string(operation) + " failed");
  }
}

void SaveJpegFixture(const lol_assistant::common::Frame& frame,
                     const std::filesystem::path& path) {
  using Microsoft::WRL::ComPtr;
  const HRESULT initialize_result =
      CoInitializeEx(nullptr, COINIT_MULTITHREADED);
  const bool uninitialize = SUCCEEDED(initialize_result);
  if (FAILED(initialize_result) && initialize_result != RPC_E_CHANGED_MODE) {
    RequireSucceeded(initialize_result, "CoInitializeEx");
  }
  try {
    ComPtr<IWICImagingFactory> factory;
    RequireSucceeded(CoCreateInstance(
                         CLSID_WICImagingFactory, nullptr,
                         CLSCTX_INPROC_SERVER,
                         IID_PPV_ARGS(factory.ReleaseAndGetAddressOf())),
                     "CoCreateInstance(WIC)");
    ComPtr<IWICStream> stream;
    RequireSucceeded(factory->CreateStream(stream.ReleaseAndGetAddressOf()),
                     "CreateStream");
    RequireSucceeded(stream->InitializeFromFilename(path.c_str(), GENERIC_WRITE),
                     "InitializeFromFilename");
    ComPtr<IWICBitmapEncoder> encoder;
    RequireSucceeded(factory->CreateEncoder(
                         GUID_ContainerFormatJpeg, nullptr,
                         encoder.ReleaseAndGetAddressOf()),
                     "CreateEncoder(JPEG)");
    RequireSucceeded(encoder->Initialize(stream.Get(), WICBitmapEncoderNoCache),
                     "Encoder Initialize");
    ComPtr<IWICBitmapFrameEncode> encoded_frame;
    ComPtr<IPropertyBag2> properties;
    RequireSucceeded(encoder->CreateNewFrame(
                         encoded_frame.ReleaseAndGetAddressOf(),
                         properties.ReleaseAndGetAddressOf()),
                     "CreateNewFrame");
    RequireSucceeded(encoded_frame->Initialize(properties.Get()),
                     "Frame Initialize");
    RequireSucceeded(encoded_frame->SetSize(frame.width, frame.height),
                     "SetSize");
    WICPixelFormatGUID format = GUID_WICPixelFormat24bppBGR;
    RequireSucceeded(encoded_frame->SetPixelFormat(&format), "SetPixelFormat");
    if (format != GUID_WICPixelFormat24bppBGR) {
      throw std::runtime_error("JPEG encoder rejected BGR24");
    }

    const UINT stride = frame.width * 3U;
    std::vector<BYTE> pixels(static_cast<std::size_t>(stride) * frame.height);
    for (std::uint32_t y = 0U; y < frame.height; ++y) {
      for (std::uint32_t x = 0U; x < frame.width; ++x) {
        const std::size_t source_offset =
            static_cast<std::size_t>(y) * frame.stride +
            static_cast<std::size_t>(x) * 4U;
        const std::size_t destination_offset =
            static_cast<std::size_t>(y) * stride +
            static_cast<std::size_t>(x) * 3U;
        pixels[destination_offset] = frame.buffer[source_offset];
        pixels[destination_offset + 1U] = frame.buffer[source_offset + 1U];
        pixels[destination_offset + 2U] = frame.buffer[source_offset + 2U];
      }
    }
    RequireSucceeded(encoded_frame->WritePixels(
                         frame.height, stride,
                         static_cast<UINT>(pixels.size()), pixels.data()),
                     "WritePixels");
    RequireSucceeded(encoded_frame->Commit(), "Frame Commit");
    RequireSucceeded(encoder->Commit(), "Encoder Commit");
  } catch (...) {
    if (uninitialize) {
      CoUninitialize();
    }
    throw;
  }
  if (uninitialize) {
    CoUninitialize();
  }
}

void TestPngRoundTrip(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test_name = "WIC PNG BGRA8 round-trip";
  const auto original = MakeFrame(5U, 3U, 17U);
  const auto path = root / L"round_trip.png";
  lol_assistant::replay::WicImageCodec::SavePng(original, path);
  const auto decoded = lol_assistant::replay::WicImageCodec::Decode(
      path, original.source, 9U);

  CHECK(test_name, decoded.width == original.width);
  CHECK(test_name, decoded.height == original.height);
  CHECK(test_name, decoded.stride == original.stride);
  CHECK(test_name, decoded.buffer == original.buffer);
  CHECK(test_name, decoded.frame_id == 9U);
}

void TestSingleFile(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test_name = "single-file replay";
  const auto path = root / L"single.png";
  lol_assistant::replay::WicImageCodec::SavePng(MakeFrame(3U, 2U, 31U), path);
  lol_assistant::replay::ImageReplaySource source(
      path, lol_assistant::replay::ReplayInputKind::SingleFile);
  CHECK(test_name, source.FrameCount() == 1U);
  const auto frame = source.TryGetNextFrame();
  CHECK(test_name, frame.has_value());
  CHECK(test_name, frame && frame->buffer.front() == 31U);
  CHECK(test_name, frame && frame->timestamps.capture_started.has_value());
  CHECK(test_name, frame && frame->timestamps.capture_completed.has_value());
  CHECK(test_name, frame && frame->timestamps.captured_at_utc.has_value());
  CHECK(test_name, !source.TryGetNextFrame().has_value());
}

void TestJpegDecode(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test_name = "WIC JPEG replay decode";
  const auto path = root / L"single.jpeg";
  SaveJpegFixture(MakeFrame(7U, 5U, 44U), path);
  lol_assistant::replay::ImageReplaySource source(
      path, lol_assistant::replay::ReplayInputKind::SingleFile);
  const auto frame = source.Next();
  CHECK(test_name, frame.has_value());
  CHECK(test_name, frame && frame->width == 7U);
  CHECK(test_name, frame && frame->height == 5U);
  CHECK(test_name, frame && frame->IsValid());
}

void TestDirectoryOrderSeekPauseAndLoop(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test_name = "directory natural order/controls";
  const auto directory = root / L"ordered";
  std::filesystem::create_directories(directory);
  lol_assistant::replay::WicImageCodec::SavePng(MakeFrame(2U, 2U, 110U),
                                                directory / L"frame10.png");
  lol_assistant::replay::WicImageCodec::SavePng(MakeFrame(2U, 2U, 102U),
                                                directory / L"frame2.png");
  lol_assistant::replay::WicImageCodec::SavePng(MakeFrame(2U, 2U, 101U),
                                                directory / L"frame1.png");
  WriteText(directory / L"ignored.txt", "not an image");

  lol_assistant::replay::ImageReplaySource source(
      directory, lol_assistant::replay::ReplayInputKind::Directory);
  CHECK(test_name, source.FrameCount() == 3U);
  auto frame = source.TryGetNextFrame();
  CHECK(test_name, frame && frame->buffer.front() == 101U);
  if (frame) {
    frame->buffer.front() = 0U;
  }

  CHECK(test_name, source.SeekIndex(2U));
  frame = source.Next();
  CHECK(test_name, frame && frame->buffer.front() == 110U);
  CHECK(test_name, !source.SeekIndex(3U));
  CHECK(test_name, source.CurrentIndex() == 3U);

  source.SetLoop(true);
  frame = source.TryGetNextFrame();
  CHECK(test_name, frame && frame->buffer.front() == 101U);
  CHECK(test_name, source.IsLooping());

  CHECK(test_name, source.SeekIndex(1U));
  source.SetPaused(true);
  CHECK(test_name, source.IsPaused());
  CHECK(test_name, !source.TryGetNextFrame().has_value());
  frame = source.Next();
  CHECK(test_name, frame && frame->buffer.front() == 102U);
  source.SetPaused(false);
}

void TestCorruptAndUnsupportedInputs(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test_name = "corrupt/unsupported rejection";
  const auto corrupt = root / L"corrupt.png";
  WriteText(corrupt, "this is not a PNG");
  CHECK(test_name, ThrowsReplayError([&corrupt] {
          lol_assistant::replay::ImageReplaySource source(
              corrupt, lol_assistant::replay::ReplayInputKind::SingleFile);
          static_cast<void>(source);
        }));

  const auto unsupported = root / L"frame.bmp";
  WriteText(unsupported, "BM");
  CHECK(test_name, ThrowsReplayError([&unsupported] {
          lol_assistant::replay::ImageReplaySource source(
              unsupported,
              lol_assistant::replay::ReplayInputKind::SingleFile);
          static_cast<void>(source);
        }));

  CHECK(test_name, ThrowsReplayError([&root] {
          lol_assistant::replay::ImageReplaySource source(
              root / L"missing.png",
              lol_assistant::replay::ReplayInputKind::SingleFile);
          static_cast<void>(source);
        }));
}

void TestManifestOrderAndDimensions(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test_name = "manifest order/dimensions";
  const auto directory = root / L"manifest_valid";
  std::filesystem::create_directories(directory);
  lol_assistant::replay::WicImageCodec::SavePng(MakeFrame(4U, 2U, 52U),
                                                directory / L"second.png");
  lol_assistant::replay::WicImageCodec::SavePng(MakeFrame(4U, 2U, 51U),
                                                directory / L"first.png");
  const auto manifest = directory / L"frames.jsonl";
  WriteText(manifest,
            "{\"path\":\"second.png\",\"width\":4,\"height\":2}\n"
            "{\"path\":\"first.png\",\"width\":4,\"height\":2}\n");

  lol_assistant::replay::ImageReplaySource source(
      manifest, lol_assistant::replay::ReplayInputKind::ManifestJsonLines);
  auto frame = source.Next();
  CHECK(test_name, frame && frame->buffer.front() == 52U);
  frame = source.Next();
  CHECK(test_name, frame && frame->buffer.front() == 51U);

  const auto bad_dimensions = directory / L"bad_dimensions.jsonl";
  WriteText(bad_dimensions,
            "{\"path\":\"first.png\",\"width\":99,\"height\":2}\n");
  CHECK(test_name, ThrowsReplayError([&bad_dimensions] {
          lol_assistant::replay::ImageReplaySource invalid(
              bad_dimensions,
              lol_assistant::replay::ReplayInputKind::ManifestJsonLines);
          static_cast<void>(invalid);
        }));
}

void TestManifestHashMismatch(const std::filesystem::path& root) {
  ++g_tests;
  constexpr std::string_view test_name = "manifest SHA-256 mismatch";
  const auto directory = root / L"manifest_hash";
  std::filesystem::create_directories(directory);
  lol_assistant::replay::WicImageCodec::SavePng(MakeFrame(2U, 2U, 77U),
                                                directory / L"hashed.png");
  const auto manifest = directory / L"bad_hash.jsonl";
  WriteText(
      manifest,
      "{\"path\":\"hashed.png\",\"sha256\":\"0000000000000000000000000000000000000000000000000000000000000000\"}\n");
  CHECK(test_name, ThrowsReplayError([&manifest] {
          lol_assistant::replay::ImageReplaySource invalid(
              manifest,
              lol_assistant::replay::ReplayInputKind::ManifestJsonLines);
          static_cast<void>(invalid);
        }));
}

}  // namespace

int main() {
  try {
    const std::filesystem::path root =
        std::filesystem::path(LOL_TEST_OUTPUT_ROOT) /
        (L"replay_cases_" + std::to_wstring(GetCurrentProcessId()));
    std::filesystem::create_directories(root);

    TestPngRoundTrip(root);
    TestSingleFile(root);
    TestJpegDecode(root);
    TestDirectoryOrderSeekPauseAndLoop(root);
    TestCorruptAndUnsupportedInputs(root);
    TestManifestOrderAndDimensions(root);
    TestManifestHashMismatch(root);
  } catch (const std::exception& error) {
    ++g_failures;
    std::cerr << "[UNCAUGHT] " << error.what() << '\n';
  }

  if (g_failures != 0) {
    std::cerr << g_failures << " replay check(s) failed across " << g_tests
              << " test cases.\n";
    return 1;
  }
  std::cout << "[PASS] " << g_tests
            << " replay test cases; all artifacts are under "
            << std::filesystem::path(LOL_TEST_OUTPUT_ROOT).string() << "\n";
  return 0;
}
