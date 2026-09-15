#include "desktop_duplication_backend.h"

#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <utility>

namespace lol_assistant::capture::detail {
namespace {

using Microsoft::WRL::ComPtr;

[[nodiscard]] LONG RectWidth(const RECT& rectangle) noexcept {
  return rectangle.right - rectangle.left;
}

[[nodiscard]] LONG RectHeight(const RECT& rectangle) noexcept {
  return rectangle.bottom - rectangle.top;
}

[[nodiscard]] std::string HResultText(const char* const operation,
                                      const HRESULT result) {
  std::ostringstream message;
  message << operation << " failed with HRESULT 0x" << std::hex
          << std::uppercase << static_cast<std::uint32_t>(result);
  return message.str();
}

class ScopedMap final {
 public:
  ScopedMap(ID3D11DeviceContext* const context,
            ID3D11Texture2D* const texture) noexcept
      : context_(context), texture_(texture) {}

  ~ScopedMap() {
    if (mapped_ && context_ != nullptr && texture_ != nullptr) {
      context_->Unmap(texture_, 0U);
    }
  }

  ScopedMap(const ScopedMap&) = delete;
  ScopedMap& operator=(const ScopedMap&) = delete;

  void MarkMapped() noexcept { mapped_ = true; }

 private:
  ID3D11DeviceContext* context_{nullptr};
  ID3D11Texture2D* texture_{nullptr};
  bool mapped_{false};
};

class DxgiDesktopDuplicationBackend final
    : public IDesktopDuplicationBackend {
 public:
  [[nodiscard]] HRESULT Initialize(
      const HMONITOR monitor, const bool recreate_device,
      std::string* const error) noexcept override {
    try {
      if (monitor == nullptr) {
        return Fail(E_INVALIDARG, "Initialize received a null monitor", error);
      }

      duplication_.Reset();
      output1_.Reset();
      staging_.Reset();
      if (recreate_device || device_ == nullptr || adapter_ == nullptr) {
        context_.Reset();
        device_.Reset();
        adapter_.Reset();
        factory_.Reset();
        const HRESULT factory_result = CreateDXGIFactory1(
            __uuidof(IDXGIFactory1),
            reinterpret_cast<void**>(factory_.GetAddressOf()));
        if (FAILED(factory_result)) {
          return Fail(factory_result, HResultText("CreateDXGIFactory1",
                                                  factory_result), error);
        }
        const HRESULT find_result = FindAdapterAndOutput(monitor, true);
        if (FAILED(find_result)) {
          return Fail(find_result, "No DXGI output matches the target monitor",
                      error);
        }

        constexpr D3D_FEATURE_LEVEL levels[]{D3D_FEATURE_LEVEL_11_0};
        D3D_FEATURE_LEVEL selected{};
        const HRESULT device_result = D3D11CreateDevice(
            adapter_.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, levels,
            static_cast<UINT>(std::size(levels)), D3D11_SDK_VERSION,
            device_.GetAddressOf(), &selected, context_.GetAddressOf());
        if (FAILED(device_result)) {
          return Fail(device_result,
                      HResultText("D3D11CreateDevice", device_result), error);
        }
      } else {
        const HRESULT find_result = FindAdapterAndOutput(monitor, false);
        if (FAILED(find_result)) {
          return Fail(find_result,
                      "The target output moved to another DXGI adapter",
                      error);
        }
      }

      const HRESULT duplicate_result = output1_->DuplicateOutput(
          device_.Get(), duplication_.GetAddressOf());
      if (FAILED(duplicate_result)) {
        return Fail(duplicate_result,
                    HResultText("IDXGIOutput1::DuplicateOutput",
                                duplicate_result),
                    error);
      }
      duplication_description_ = {};
      duplication_->GetDesc(&duplication_description_);
      monitor_ = monitor;
      LARGE_INTEGER frequency{};
      if (QueryPerformanceFrequency(&frequency) != FALSE) {
        performance_counter_frequency_ = frequency.QuadPart;
      } else {
        performance_counter_frequency_ = 0;
      }
      if (error != nullptr) {
        error->clear();
      }
      return S_OK;
    } catch (...) {
      return Fail(E_FAIL, "Unexpected DXGI initialization failure", error);
    }
  }

  [[nodiscard]] DesktopAcquireResult AcquireNextFrame(
      const std::chrono::milliseconds timeout,
      const DesktopCaptureGeometry& geometry) noexcept override {
    DesktopAcquireResult result{};
    if (duplication_ == nullptr || device_ == nullptr || context_ == nullptr) {
      result.result = E_UNEXPECTED;
      result.diagnostic = "Desktop Duplication is not initialized";
      return result;
    }
    if (!geometry.IsReady() || geometry.monitor != monitor_) {
      result.result = E_INVALIDARG;
      result.diagnostic = "Capture geometry does not match the duplicated output";
      return result;
    }

    const auto bounded_timeout = std::clamp<std::int64_t>(
        timeout.count(), 0, static_cast<std::int64_t>(
                                std::numeric_limits<UINT>::max()));
    DXGI_OUTDUPL_FRAME_INFO frame_info{};
    ComPtr<IDXGIResource> resource;
    result.result = duplication_->AcquireNextFrame(
        static_cast<UINT>(bounded_timeout), &frame_info,
        resource.GetAddressOf());
    if (FAILED(result.result)) {
      if (result.result != DXGI_ERROR_WAIT_TIMEOUT) {
        result.diagnostic =
            HResultText("IDXGIOutputDuplication::AcquireNextFrame",
                        result.result);
      }
      return result;
    }
    result.frame_acquired = true;

    try {
      ComPtr<ID3D11Texture2D> source;
      result.result = resource.As(&source);
      if (FAILED(result.result)) {
        result.diagnostic = HResultText("Query ID3D11Texture2D", result.result);
        return result;
      }

      D3D11_TEXTURE2D_DESC source_description{};
      source->GetDesc(&source_description);
      if (source_description.Format != DXGI_FORMAT_B8G8R8A8_UNORM &&
          source_description.Format != DXGI_FORMAT_B8G8R8A8_UNORM_SRGB) {
        result.result = DXGI_ERROR_UNSUPPORTED;
        result.diagnostic = "Desktop Duplication returned a non-BGRA8 texture";
        return result;
      }
      result.result = EnsureStaging(source_description);
      if (FAILED(result.result)) {
        result.diagnostic =
            HResultText("Create Desktop Duplication staging texture",
                        result.result);
        return result;
      }

      context_->CopyResource(staging_.Get(), source.Get());
      const HRESULT device_status = device_->GetDeviceRemovedReason();
      if (FAILED(device_status)) {
        result.result = device_status;
        result.diagnostic = HResultText("Desktop Duplication device",
                                        device_status);
        return result;
      }

      D3D11_MAPPED_SUBRESOURCE mapped{};
      result.result = context_->Map(staging_.Get(), 0U, D3D11_MAP_READ, 0U,
                                    &mapped);
      if (FAILED(result.result)) {
        result.diagnostic = HResultText("Map Desktop Duplication staging",
                                        result.result);
        return result;
      }
      ScopedMap map_guard(context_.Get(), staging_.Get());
      map_guard.MarkMapped();
      result.result = ConvertMappedFrame(source_description, mapped, geometry,
                                         frame_info, &result.frame);
      if (FAILED(result.result)) {
        result.diagnostic = "Unable to normalize and crop duplicated pixels";
        return result;
      }
      result.diagnostic.clear();
      return result;
    } catch (const std::exception& exception) {
      result.result = E_FAIL;
      result.diagnostic = exception.what();
      return result;
    } catch (...) {
      result.result = E_FAIL;
      result.diagnostic = "Unexpected Desktop Duplication conversion failure";
      return result;
    }
  }

  [[nodiscard]] HRESULT ReleaseFrame() noexcept override {
    if (duplication_ == nullptr) {
      return E_UNEXPECTED;
    }
    return duplication_->ReleaseFrame();
  }

  void Shutdown() noexcept override {
    duplication_.Reset();
    output1_.Reset();
    staging_.Reset();
    context_.Reset();
    device_.Reset();
    adapter_.Reset();
    factory_.Reset();
    monitor_ = nullptr;
    output_description_ = {};
    duplication_description_ = {};
  }

 private:
  [[nodiscard]] HRESULT FindAdapterAndOutput(
      const HMONITOR monitor, const bool search_all_adapters) noexcept {
    if (factory_ == nullptr) {
      return E_UNEXPECTED;
    }

    ComPtr<IDXGIAdapter1> selected_adapter;
    ComPtr<IDXGIOutput1> selected_output;
    DXGI_OUTPUT_DESC selected_description{};
    for (UINT adapter_index = 0U;; ++adapter_index) {
      ComPtr<IDXGIAdapter1> candidate_adapter;
      HRESULT adapter_result = S_OK;
      if (search_all_adapters) {
        adapter_result = factory_->EnumAdapters1(
            adapter_index, candidate_adapter.GetAddressOf());
      } else if (adapter_index == 0U && adapter_ != nullptr) {
        candidate_adapter = adapter_;
      } else {
        break;
      }
      if (adapter_result == DXGI_ERROR_NOT_FOUND) {
        break;
      }
      if (FAILED(adapter_result)) {
        return adapter_result;
      }

      for (UINT output_index = 0U;; ++output_index) {
        ComPtr<IDXGIOutput> candidate_output;
        const HRESULT output_result = candidate_adapter->EnumOutputs(
            output_index, candidate_output.GetAddressOf());
        if (output_result == DXGI_ERROR_NOT_FOUND) {
          break;
        }
        if (FAILED(output_result)) {
          return output_result;
        }
        DXGI_OUTPUT_DESC description{};
        const HRESULT description_result =
            candidate_output->GetDesc(&description);
        if (FAILED(description_result)) {
          continue;
        }
        if (description.Monitor != monitor) {
          continue;
        }
        const HRESULT output1_result = candidate_output.As(&selected_output);
        if (FAILED(output1_result)) {
          return output1_result;
        }
        selected_adapter = candidate_adapter;
        selected_description = description;
        break;
      }
      if (selected_output != nullptr) {
        break;
      }
    }
    if (selected_output == nullptr || selected_adapter == nullptr) {
      return DXGI_ERROR_NOT_FOUND;
    }
    adapter_ = std::move(selected_adapter);
    output1_ = std::move(selected_output);
    output_description_ = selected_description;
    return S_OK;
  }

  [[nodiscard]] HRESULT EnsureStaging(
      const D3D11_TEXTURE2D_DESC& source) noexcept {
    if (staging_ != nullptr) {
      D3D11_TEXTURE2D_DESC existing{};
      staging_->GetDesc(&existing);
      if (existing.Width == source.Width && existing.Height == source.Height &&
          existing.Format == source.Format) {
        return S_OK;
      }
      staging_.Reset();
    }

    D3D11_TEXTURE2D_DESC description{};
    description.Width = source.Width;
    description.Height = source.Height;
    description.MipLevels = 1U;
    description.ArraySize = 1U;
    description.Format = source.Format;
    description.SampleDesc.Count = 1U;
    description.Usage = D3D11_USAGE_STAGING;
    description.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    return device_->CreateTexture2D(&description, nullptr,
                                    staging_.GetAddressOf());
  }

  [[nodiscard]] HRESULT ConvertMappedFrame(
      const D3D11_TEXTURE2D_DESC& source,
      const D3D11_MAPPED_SUBRESOURCE& mapped,
      const DesktopCaptureGeometry& geometry,
      const DXGI_OUTDUPL_FRAME_INFO& frame_info,
      DesktopBackendFrame* const output) const {
    if (output == nullptr || mapped.pData == nullptr ||
        RectWidth(geometry.source_rect) <= 0 ||
        RectHeight(geometry.source_rect) <= 0) {
      return E_INVALIDARG;
    }
    const auto crop_width =
        static_cast<std::uint32_t>(RectWidth(geometry.source_rect));
    const auto crop_height =
        static_cast<std::uint32_t>(RectHeight(geometry.source_rect));
    constexpr auto bytes_per_pixel =
        static_cast<std::uint32_t>(common::Frame::kBytesPerPixel);
    if (crop_width > std::numeric_limits<std::uint32_t>::max() /
                         bytes_per_pixel) {
      return E_INVALIDARG;
    }
    const std::uint32_t output_stride = crop_width * bytes_per_pixel;
    const std::size_t output_size =
        static_cast<std::size_t>(output_stride) * crop_height;
    if (crop_height != 0U && output_size / crop_height != output_stride) {
      return E_INVALIDARG;
    }

    const auto screen_width =
        static_cast<std::uint32_t>(RectWidth(geometry.output_physical));
    const auto screen_height =
        static_cast<std::uint32_t>(RectHeight(geometry.output_physical));
    const bool quarter_turn =
        output_description_.Rotation == DXGI_MODE_ROTATION_ROTATE90 ||
        output_description_.Rotation == DXGI_MODE_ROTATION_ROTATE270;
    const std::uint32_t expected_width =
        quarter_turn ? source.Height : source.Width;
    const std::uint32_t expected_height =
        quarter_turn ? source.Width : source.Height;
    if (screen_width != expected_width || screen_height != expected_height ||
        mapped.RowPitch < source.Width * bytes_per_pixel) {
      return E_INVALIDARG;
    }

    output->width = crop_width;
    output->height = crop_height;
    output->stride = output_stride;
    output->buffer.resize(output_size);
    output->protected_content_masked =
        frame_info.ProtectedContentMaskedOut != FALSE;
    if (frame_info.LastPresentTime.QuadPart > 0 &&
        performance_counter_frequency_ > 0) {
      const long double nanoseconds =
          static_cast<long double>(frame_info.LastPresentTime.QuadPart) *
          1000000000.0L /
          static_cast<long double>(performance_counter_frequency_);
      output->source_timestamp = std::chrono::nanoseconds{
          static_cast<std::chrono::nanoseconds::rep>(nanoseconds)};
    }

    const auto* const input =
        static_cast<const std::uint8_t*>(mapped.pData);
    for (std::uint32_t y = 0U; y < crop_height; ++y) {
      for (std::uint32_t x = 0U; x < crop_width; ++x) {
        const std::uint32_t screen_x =
            static_cast<std::uint32_t>(geometry.source_rect.left) + x;
        const std::uint32_t screen_y =
            static_cast<std::uint32_t>(geometry.source_rect.top) + y;
        std::uint32_t raw_x = screen_x;
        std::uint32_t raw_y = screen_y;
        switch (output_description_.Rotation) {
          case DXGI_MODE_ROTATION_UNSPECIFIED:
          case DXGI_MODE_ROTATION_IDENTITY:
            break;
          case DXGI_MODE_ROTATION_ROTATE90:
            raw_x = screen_y;
            raw_y = source.Height - 1U - screen_x;
            break;
          case DXGI_MODE_ROTATION_ROTATE180:
            raw_x = source.Width - 1U - screen_x;
            raw_y = source.Height - 1U - screen_y;
            break;
          case DXGI_MODE_ROTATION_ROTATE270:
            raw_x = source.Width - 1U - screen_y;
            raw_y = screen_x;
            break;
          default:
            return DXGI_ERROR_UNSUPPORTED;
        }
        if (raw_x >= source.Width || raw_y >= source.Height) {
          return E_INVALIDARG;
        }
        const auto* const source_pixel =
            input + (static_cast<std::size_t>(raw_y) * mapped.RowPitch) +
            (static_cast<std::size_t>(raw_x) * bytes_per_pixel);
        auto* const destination_pixel =
            output->buffer.data() +
            (static_cast<std::size_t>(y) * output_stride) +
            (static_cast<std::size_t>(x) * bytes_per_pixel);
        std::memcpy(destination_pixel, source_pixel, bytes_per_pixel);
      }
    }
    return S_OK;
  }

  [[nodiscard]] HRESULT Fail(const HRESULT result, std::string message,
                             std::string* const error) noexcept {
    if (error != nullptr) {
      try {
        *error = std::move(message);
      } catch (...) {
        *error = "DXGI Desktop Duplication failure";
      }
    }
    return result;
  }

  ComPtr<IDXGIFactory1> factory_{};
  ComPtr<IDXGIAdapter1> adapter_{};
  ComPtr<IDXGIOutput1> output1_{};
  ComPtr<ID3D11Device> device_{};
  ComPtr<ID3D11DeviceContext> context_{};
  ComPtr<IDXGIOutputDuplication> duplication_{};
  ComPtr<ID3D11Texture2D> staging_{};
  HMONITOR monitor_{nullptr};
  DXGI_OUTPUT_DESC output_description_{};
  DXGI_OUTDUPL_DESC duplication_description_{};
  std::int64_t performance_counter_frequency_{0};
};

}  // namespace

std::unique_ptr<IDesktopDuplicationBackend>
CreateDxgiDesktopDuplicationBackend() {
  return std::make_unique<DxgiDesktopDuplicationBackend>();
}

}  // namespace lol_assistant::capture::detail
