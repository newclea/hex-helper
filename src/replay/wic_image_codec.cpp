#include "lol_assistant/replay/wic_image_codec.h"

#include <Windows.h>
#include <wincodec.h>
#include <wrl/client.h>

#include <algorithm>
#include <array>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

namespace lol_assistant::replay {
namespace {

using Microsoft::WRL::ComPtr;

class ComApartment final {
public:
  ComApartment() {
    const HRESULT result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (result == RPC_E_CHANGED_MODE) {
      return;
    }
    if (FAILED(result)) {
      ThrowHResult("CoInitializeEx", result);
    }
    must_uninitialize_ = true;
  }

  ~ComApartment() {
    if (must_uninitialize_) {
      CoUninitialize();
    }
  }

  ComApartment(const ComApartment &) = delete;
  ComApartment &operator=(const ComApartment &) = delete;

private:
  [[noreturn]] static void ThrowHResult(const char *operation,
                                        const HRESULT result) {
    std::ostringstream message;
    message << operation << " failed with HRESULT 0x" << std::hex
            << static_cast<unsigned long>(result);
    throw std::runtime_error(message.str());
  }

  bool must_uninitialize_{false};
};

[[noreturn]] void ThrowHResult(const char *operation, const HRESULT result,
                               const std::filesystem::path &path) {
  std::ostringstream message;
  message << operation << " failed for '" << path.string()
          << "' with HRESULT 0x" << std::hex
          << static_cast<unsigned long>(result);
  throw std::runtime_error(message.str());
}

[[nodiscard]] ComPtr<IWICImagingFactory> CreateFactory() {
  ComPtr<IWICImagingFactory> factory;
  const HRESULT result =
      CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                       IID_PPV_ARGS(factory.ReleaseAndGetAddressOf()));
  if (FAILED(result)) {
    ThrowHResult("CoCreateInstance(CLSID_WICImagingFactory)", result, {});
  }
  return factory;
}

void AppendBigEndian(std::vector<std::uint8_t> &output,
                     const std::uint32_t value) {
  output.push_back(static_cast<std::uint8_t>((value >> 24U) & 0xFFU));
  output.push_back(static_cast<std::uint8_t>((value >> 16U) & 0xFFU));
  output.push_back(static_cast<std::uint8_t>((value >> 8U) & 0xFFU));
  output.push_back(static_cast<std::uint8_t>(value & 0xFFU));
}

[[nodiscard]] const std::array<std::uint32_t, 256U> &CrcTable() {
  static const std::array<std::uint32_t, 256U> table = [] {
    std::array<std::uint32_t, 256U> values{};
    for (std::uint32_t index = 0U; index < values.size(); ++index) {
      std::uint32_t crc = index;
      for (std::uint32_t bit = 0U; bit < 8U; ++bit) {
        crc = (crc & 1U) != 0U ? 0xEDB88320U ^ (crc >> 1U) : crc >> 1U;
      }
      values[index] = crc;
    }
    return values;
  }();
  return table;
}

[[nodiscard]] std::uint32_t UpdateCrc(std::uint32_t crc,
                                      const std::uint8_t *bytes,
                                      const std::size_t size) {
  const auto &table = CrcTable();
  for (std::size_t index = 0U; index < size; ++index) {
    crc = table[(crc ^ bytes[index]) & 0xFFU] ^ (crc >> 8U);
  }
  return crc;
}

[[nodiscard]] std::uint32_t Adler32(const std::vector<std::uint8_t> &bytes) {
  constexpr std::uint32_t kModulus = 65'521U;
  constexpr std::size_t kMaximumChunk = 5'552U;
  std::uint32_t first = 1U;
  std::uint32_t second = 0U;
  std::size_t offset = 0U;
  while (offset < bytes.size()) {
    const std::size_t end = std::min(bytes.size(), offset + kMaximumChunk);
    for (; offset < end; ++offset) {
      first += bytes[offset];
      second += first;
    }
    first %= kModulus;
    second %= kModulus;
  }
  return (second << 16U) | first;
}

void WriteChunk(std::ofstream &output, const std::array<char, 4U> &type,
                const std::vector<std::uint8_t> &payload) {
  if (payload.size() > std::numeric_limits<std::uint32_t>::max()) {
    throw std::invalid_argument("PNG chunk exceeds the 32-bit length limit");
  }
  std::vector<std::uint8_t> length;
  length.reserve(4U);
  AppendBigEndian(length, static_cast<std::uint32_t>(payload.size()));
  output.write(reinterpret_cast<const char *>(length.data()),
               static_cast<std::streamsize>(length.size()));
  output.write(type.data(), static_cast<std::streamsize>(type.size()));
  if (!payload.empty()) {
    output.write(reinterpret_cast<const char *>(payload.data()),
                 static_cast<std::streamsize>(payload.size()));
  }
  std::uint32_t crc = 0xFFFFFFFFU;
  crc = UpdateCrc(crc, reinterpret_cast<const std::uint8_t *>(type.data()),
                  type.size());
  crc = UpdateCrc(crc, payload.data(), payload.size()) ^ 0xFFFFFFFFU;
  std::vector<std::uint8_t> encoded_crc;
  encoded_crc.reserve(4U);
  AppendBigEndian(encoded_crc, crc);
  output.write(reinterpret_cast<const char *>(encoded_crc.data()),
               static_cast<std::streamsize>(encoded_crc.size()));
  if (!output) {
    throw std::runtime_error("Failed while writing PNG chunk");
  }
}

[[nodiscard]] std::vector<std::uint8_t>
PngScanlines(const common::Frame &frame) {
  const std::uint64_t row_size =
      1ULL + static_cast<std::uint64_t>(frame.width) * 4ULL;
  const std::uint64_t total_size = row_size * frame.height;
  if (total_size > std::numeric_limits<std::size_t>::max()) {
    throw std::invalid_argument("PNG scanline buffer is too large");
  }
  std::vector<std::uint8_t> scanlines(static_cast<std::size_t>(total_size));
  for (std::uint32_t row = 0U; row < frame.height; ++row) {
    const auto *source =
        frame.buffer.data() + static_cast<std::size_t>(row) * frame.stride;
    auto *destination =
        scanlines.data() +
        static_cast<std::size_t>(row) * static_cast<std::size_t>(row_size);
    *destination++ = 0U; // PNG filter NONE.
    for (std::uint32_t column = 0U; column < frame.width; ++column) {
      // Owning frames are BGRA8; PNG color type 6 stores RGBA8.
      destination[0U] = source[2U];
      destination[1U] = source[1U];
      destination[2U] = source[0U];
      destination[3U] = source[3U];
      source += 4U;
      destination += 4U;
    }
  }
  return scanlines;
}

[[nodiscard]] std::vector<std::uint8_t>
StoreOnlyZlib(const std::vector<std::uint8_t> &bytes) {
  constexpr std::size_t kMaximumBlock = 65'535U;
  const std::size_t blocks = std::max<std::size_t>(
      1U, (bytes.size() + kMaximumBlock - 1U) / kMaximumBlock);
  const std::uint64_t reserved = 2ULL + bytes.size() + blocks * 5ULL + 4ULL;
  if (reserved > std::numeric_limits<std::uint32_t>::max()) {
    throw std::invalid_argument("PNG IDAT payload exceeds 32-bit limit");
  }
  std::vector<std::uint8_t> output;
  output.reserve(static_cast<std::size_t>(reserved));
  output.push_back(0x78U);
  output.push_back(0x01U); // zlib, 32K window, fastest/no compression.
  std::size_t offset = 0U;
  do {
    const std::size_t remaining = bytes.size() - offset;
    const std::uint16_t length = static_cast<std::uint16_t>(
        std::min<std::size_t>(remaining, kMaximumBlock));
    const bool final_block = offset + length == bytes.size();
    output.push_back(final_block ? 0x01U : 0x00U);
    output.push_back(static_cast<std::uint8_t>(length & 0xFFU));
    output.push_back(static_cast<std::uint8_t>((length >> 8U) & 0xFFU));
    const std::uint16_t inverse = static_cast<std::uint16_t>(~length);
    output.push_back(static_cast<std::uint8_t>(inverse & 0xFFU));
    output.push_back(static_cast<std::uint8_t>((inverse >> 8U) & 0xFFU));
    output.insert(output.end(),
                  bytes.begin() + static_cast<std::ptrdiff_t>(offset),
                  bytes.begin() + static_cast<std::ptrdiff_t>(offset + length));
    offset += length;
  } while (offset < bytes.size());
  AppendBigEndian(output, Adler32(bytes));
  return output;
}

} // namespace

common::Frame WicImageCodec::Decode(const std::filesystem::path &path,
                                    common::FrameSource source,
                                    const std::uint64_t frame_id) {
  if (!source.IsValid() || source.kind == common::FrameSourceKind::Unknown) {
    throw std::invalid_argument("WIC decode requires a valid frame source");
  }
  if (!std::filesystem::is_regular_file(path)) {
    throw std::invalid_argument("Image is not a regular file: " +
                                path.string());
  }

  const ComApartment apartment;
  const auto factory = CreateFactory();

  ComPtr<IWICBitmapDecoder> decoder;
  HRESULT result = factory->CreateDecoderFromFilename(
      path.c_str(), nullptr, GENERIC_READ, WICDecodeMetadataCacheOnLoad,
      decoder.ReleaseAndGetAddressOf());
  if (FAILED(result)) {
    ThrowHResult("IWICImagingFactory::CreateDecoderFromFilename", result, path);
  }

  UINT frame_count = 0U;
  result = decoder->GetFrameCount(&frame_count);
  if (FAILED(result)) {
    ThrowHResult("IWICBitmapDecoder::GetFrameCount", result, path);
  }
  if (frame_count == 0U) {
    throw std::runtime_error("Image contains no decodable frames: " +
                             path.string());
  }

  ComPtr<IWICBitmapFrameDecode> decoded_frame;
  result = decoder->GetFrame(0U, decoded_frame.ReleaseAndGetAddressOf());
  if (FAILED(result)) {
    ThrowHResult("IWICBitmapDecoder::GetFrame", result, path);
  }

  UINT width = 0U;
  UINT height = 0U;
  result = decoded_frame->GetSize(&width, &height);
  if (FAILED(result)) {
    ThrowHResult("IWICBitmapSource::GetSize", result, path);
  }
  if (width == 0U || height == 0U ||
      width > std::numeric_limits<std::uint32_t>::max() /
                  common::Frame::kBytesPerPixel) {
    throw std::runtime_error("Image dimensions are invalid or too large: " +
                             path.string());
  }

  ComPtr<IWICFormatConverter> converter;
  result = factory->CreateFormatConverter(converter.ReleaseAndGetAddressOf());
  if (FAILED(result)) {
    ThrowHResult("IWICImagingFactory::CreateFormatConverter", result, path);
  }

  BOOL can_convert = FALSE;
  WICPixelFormatGUID source_format{};
  result = decoded_frame->GetPixelFormat(&source_format);
  if (FAILED(result)) {
    ThrowHResult("IWICBitmapSource::GetPixelFormat", result, path);
  }
  result = converter->CanConvert(source_format, GUID_WICPixelFormat32bppBGRA,
                                 &can_convert);
  if (FAILED(result) || can_convert == FALSE) {
    throw std::runtime_error("Image cannot be converted to BGRA8: " +
                             path.string());
  }

  result = converter->Initialize(
      decoded_frame.Get(), GUID_WICPixelFormat32bppBGRA,
      WICBitmapDitherTypeNone, nullptr, 0.0, WICBitmapPaletteTypeCustom);
  if (FAILED(result)) {
    ThrowHResult("IWICFormatConverter::Initialize", result, path);
  }

  common::Frame frame;
  frame.source = std::move(source);
  frame.frame_id = frame_id;
  frame.width = width;
  frame.height = height;
  frame.stride =
      width * static_cast<std::uint32_t>(common::Frame::kBytesPerPixel);
  const auto required_size = frame.RequiredBufferSize();
  if (!required_size.has_value() ||
      *required_size >
          static_cast<std::size_t>(std::numeric_limits<UINT>::max())) {
    throw std::runtime_error("Decoded image buffer is too large for WIC: " +
                             path.string());
  }
  frame.buffer.resize(*required_size);

  result = converter->CopyPixels(nullptr, frame.stride,
                                 static_cast<UINT>(*required_size),
                                 frame.buffer.data());
  if (FAILED(result)) {
    ThrowHResult("IWICBitmapSource::CopyPixels", result, path);
  }
  if (!frame.IsValid()) {
    throw std::runtime_error("WIC produced an invalid BGRA8 frame: " +
                             path.string());
  }
  return frame;
}

void WicImageCodec::SavePng(const common::Frame &frame,
                            const std::filesystem::path &path) {
  if (!frame.IsValid()) {
    throw std::invalid_argument("Cannot encode an invalid frame");
  }
  if (path.extension() != L".png" && path.extension() != L".PNG") {
    throw std::invalid_argument("WIC PNG output must use a .png extension: " +
                                path.string());
  }
  if (std::filesystem::exists(path)) {
    throw std::runtime_error("Refusing to overwrite existing PNG: " +
                             path.string());
  }
  if (!path.parent_path().empty()) {
    std::filesystem::create_directories(path.parent_path());
  }

  const auto scanlines = PngScanlines(frame);
  const auto idat = StoreOnlyZlib(scanlines);
  std::ofstream output(path, std::ios::binary | std::ios::out);
  if (!output) {
    throw std::runtime_error("Unable to create PNG: " + path.string());
  }
  constexpr std::array<std::uint8_t, 8U> signature{0x89U, 0x50U, 0x4EU, 0x47U,
                                                   0x0DU, 0x0AU, 0x1AU, 0x0AU};
  output.write(reinterpret_cast<const char *>(signature.data()),
               static_cast<std::streamsize>(signature.size()));

  std::vector<std::uint8_t> header;
  header.reserve(13U);
  AppendBigEndian(header, frame.width);
  AppendBigEndian(header, frame.height);
  header.push_back(8U); // bit depth
  header.push_back(6U); // RGBA
  header.push_back(0U); // compression
  header.push_back(0U); // filter
  header.push_back(0U); // no interlace
  WriteChunk(output, {'I', 'H', 'D', 'R'}, header);
  WriteChunk(output, {'I', 'D', 'A', 'T'}, idat);
  WriteChunk(output, {'I', 'E', 'N', 'D'}, {});
  output.flush();
  if (!output) {
    throw std::runtime_error("Failed while writing PNG: " + path.string());
  }
}

} // namespace lol_assistant::replay
