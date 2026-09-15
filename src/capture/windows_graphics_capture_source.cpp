#include "lol_assistant/capture/windows_graphics_capture_source.h"

#include <d3d11.h>
#include <dxgi1_2.h>
#include <inspectable.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>

#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <winrt/base.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <future>
#include <iomanip>
#include <limits>
#include <mutex>
#include <sstream>
#include <string_view>
#include <thread>
#include <utility>

#include "lol_assistant/capture/bounded_queue.h"

namespace lol_assistant::capture {
namespace {

namespace wgc = winrt::Windows::Graphics::Capture;
namespace directx = winrt::Windows::Graphics::DirectX;
namespace direct3d11 = winrt::Windows::Graphics::DirectX::Direct3D11;

constexpr auto kPixelFormat =
    directx::DirectXPixelFormat::B8G8R8A8UIntNormalized;
constexpr int32_t kFramePoolBufferCount = 2;
constexpr std::size_t kConversionSampleCount = 120U;

struct StartResult final {
  bool succeeded{false};
  std::string message{};
};

struct EpochCaptureFrame final {
  wgc::Direct3D11CaptureFrame frame{nullptr};
  std::uint64_t callback_epoch{0U};
};

struct D3dDeviceBundle final {
  winrt::com_ptr<ID3D11Device> device{};
  winrt::com_ptr<ID3D11DeviceContext> context{};
  direct3d11::IDirect3DDevice winrt_device{nullptr};
};

class CaptureFrameGuard final {
 public:
  explicit CaptureFrameGuard(wgc::Direct3D11CaptureFrame& frame) noexcept
      : frame_(&frame) {}

  ~CaptureFrameGuard() { Close(); }

  CaptureFrameGuard(const CaptureFrameGuard&) = delete;
  CaptureFrameGuard& operator=(const CaptureFrameGuard&) = delete;

  void Close() noexcept {
    if (frame_ == nullptr) {
      return;
    }
    try {
      frame_->Close();
    } catch (...) {
    }
    frame_ = nullptr;
  }

 private:
  wgc::Direct3D11CaptureFrame* frame_;
};

[[nodiscard]] std::string NarrowMessage(const std::wstring_view wide_message) {
  std::ostringstream stream;
  for (const wchar_t character : wide_message) {
    stream << (character >= 0 && character <= 0x7f
                   ? static_cast<char>(character)
                   : '?');
  }
  return stream.str();
}

[[nodiscard]] std::string HResultMessage(const winrt::hresult_error& error) {
  std::ostringstream stream;
  stream << "HRESULT 0x" << std::hex << std::uppercase
         << static_cast<std::uint32_t>(error.code().value) << ": "
         << NarrowMessage(error.message());
  return stream.str();
}

[[nodiscard]] std::string WindowSourceId(const HWND window) {
  std::ostringstream stream;
  stream << "hwnd:0x" << std::hex << std::uppercase
         << reinterpret_cast<std::uintptr_t>(window);
  return stream.str();
}

[[nodiscard]] wgc::GraphicsCaptureItem CreateItemForWindow(
    const HWND window) {
  auto interop = winrt::get_activation_factory<wgc::GraphicsCaptureItem,
                                                IGraphicsCaptureItemInterop>();
  wgc::GraphicsCaptureItem item{nullptr};
  winrt::check_hresult(interop->CreateForWindow(
      window, winrt::guid_of<wgc::IGraphicsCaptureItem>(),
      reinterpret_cast<void**>(winrt::put_abi(item))));
  return item;
}

[[nodiscard]] D3dDeviceBundle CreateD3dDevice() {
  D3dDeviceBundle bundle;
  constexpr D3D_FEATURE_LEVEL feature_levels[]{D3D_FEATURE_LEVEL_11_0};
  D3D_FEATURE_LEVEL selected_level{};

  HRESULT result = D3D11CreateDevice(
      nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
      D3D11_CREATE_DEVICE_BGRA_SUPPORT, feature_levels,
      static_cast<UINT>(std::size(feature_levels)), D3D11_SDK_VERSION,
      bundle.device.put(), &selected_level, bundle.context.put());
  if (result == DXGI_ERROR_UNSUPPORTED) {
    result = D3D11CreateDevice(
        nullptr, D3D_DRIVER_TYPE_WARP, nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT, feature_levels,
        static_cast<UINT>(std::size(feature_levels)), D3D11_SDK_VERSION,
        bundle.device.put(), &selected_level, bundle.context.put());
  }
  winrt::check_hresult(result);

  const auto dxgi_device = bundle.device.as<IDXGIDevice>();
  winrt::com_ptr<IInspectable> inspectable_device;
  winrt::check_hresult(CreateDirect3D11DeviceFromDXGIDevice(
      dxgi_device.get(), inspectable_device.put()));
  bundle.winrt_device =
      inspectable_device.as<direct3d11::IDirect3DDevice>();
  return bundle;
}

[[nodiscard]] winrt::Windows::Graphics::SizeInt32 SafePoolSize(
    const winrt::Windows::Graphics::SizeInt32 size) noexcept {
  return {std::max(size.Width, 1), std::max(size.Height, 1)};
}

[[nodiscard]] double Percentile(
    std::array<double, kConversionSampleCount>& samples,
    const std::size_t sample_count, const double percentile) noexcept {
  if (sample_count == 0U) {
    return 0.0;
  }
  std::sort(samples.begin(), samples.begin() + sample_count);
  const double position =
      percentile * static_cast<double>(sample_count - 1U);
  const auto lower = static_cast<std::size_t>(position);
  const auto upper = std::min(lower + 1U, sample_count - 1U);
  const double fraction = position - static_cast<double>(lower);
  return samples[lower] + ((samples[upper] - samples[lower]) * fraction);
}

}  // namespace

std::uint64_t detail::WgcCapturedFrameMailbox::BeginCaptureEpoch() noexcept {
  std::scoped_lock lock(mutex_);
  if (current_epoch_ != std::numeric_limits<std::uint64_t>::max()) {
    ++current_epoch_;
  }
  latest_.reset();
  return current_epoch_;
}

void detail::WgcCapturedFrameMailbox::Clear() noexcept {
  std::scoped_lock lock(mutex_);
  latest_.reset();
}

bool detail::WgcCapturedFrameMailbox::IsCurrent(
    const std::uint64_t epoch) const noexcept {
  std::scoped_lock lock(mutex_);
  return epoch != 0U && epoch == current_epoch_;
}

std::uint64_t detail::WgcCapturedFrameMailbox::CurrentCaptureEpoch()
    const noexcept {
  std::scoped_lock lock(mutex_);
  return current_epoch_;
}

detail::WgcFramePublishResult detail::WgcCapturedFrameMailbox::Publish(
    const std::uint64_t callback_epoch, common::CapturedFrame frame) {
  std::scoped_lock lock(mutex_);
  if (callback_epoch == 0U || callback_epoch != current_epoch_) {
    return WgcFramePublishResult::RejectedEpoch;
  }
  if (!frame.IsValid() ||
      frame.identity.capture_epoch != callback_epoch) {
    return WgcFramePublishResult::InvalidFrame;
  }
  const bool replaced = latest_.has_value();
  latest_ = std::move(frame);
  return replaced ? WgcFramePublishResult::Replaced
                  : WgcFramePublishResult::Published;
}

std::optional<common::CapturedFrame>
detail::WgcCapturedFrameMailbox::Take() {
  std::scoped_lock lock(mutex_);
  if (!latest_.has_value()) {
    return std::nullopt;
  }
  std::optional<common::CapturedFrame> result{std::move(*latest_)};
  latest_.reset();
  return result;
}

class WindowsGraphicsCaptureSource::Impl final
    : public std::enable_shared_from_this<Impl> {
 public:
  Impl(const HWND window, const std::size_t requested_capacity)
      : window_(window),
        queue_capacity_(std::clamp(requested_capacity, std::size_t{1U},
                                   kMaximumQueueCapacity)),
        source_{common::FrameSourceKind::WindowsGraphicsCapture,
                WindowSourceId(window)},
        raw_frames_(queue_capacity_, QueueOverflowPolicy::DropOldest) {
    statistics_.queue_capacity = queue_capacity_;
  }

  ~Impl() { Stop(); }

  [[nodiscard]] bool Start(std::string* const error) noexcept {
    std::scoped_lock lifecycle_lock(lifecycle_mutex_);
    const CaptureState current_state = lifecycle_.State();
    if (current_state == CaptureState::Running ||
        current_state == CaptureState::Paused) {
      if (error != nullptr) {
        error->clear();
      }
      return true;
    }

    JoinExistingWorker();
    const std::uint64_t capture_epoch = ResetForStart();
    lifecycle_.BeginStarting();

    if (window_ == nullptr || IsWindow(window_) == FALSE) {
      FailCapture(detail::CaptureFailure{
          detail::CaptureFailureStage::Startup, std::nullopt,
          "The capture HWND is null or no longer identifies a window"});
      if (error != nullptr) {
        *error = LastError();
      }
      return false;
    }

    try {
      std::promise<StartResult> startup_promise;
      auto startup_future = startup_promise.get_future();
      const std::shared_ptr<Impl> self = shared_from_this();
      worker_ = std::thread(
          [self, capture_epoch,
           promise = std::move(startup_promise)]() mutable {
            self->WorkerMain(std::move(promise), capture_epoch);
          });

      const StartResult result = startup_future.get();
      if (!result.succeeded) {
        if (worker_.joinable()) {
          worker_.join();
        }
        if (error != nullptr) {
          *error = result.message;
        }
        return false;
      }
      if (error != nullptr) {
        error->clear();
      }
      return true;
    } catch (const std::exception& exception) {
      FailCapture(detail::CaptureFailure{
          detail::CaptureFailureStage::Startup, std::nullopt,
          exception.what()});
    } catch (...) {
      FailCapture(detail::CaptureFailure{
          detail::CaptureFailureStage::Startup, std::nullopt,
          "Unable to create the capture worker thread"});
    }

    RequestWorkerStop();
    if (worker_.joinable()) {
      worker_.join();
    }
    if (error != nullptr) {
      *error = LastError();
    }
    return false;
  }

  void Stop() noexcept {
    std::scoped_lock lifecycle_lock(lifecycle_mutex_);
    lifecycle_.BeginStopping();
    if (!worker_.joinable()) {
      static_cast<void>(DrainRawFrames());
      captured_frames_.Clear();
      lifecycle_.MarkStopped();
      return;
    }

    RequestWorkerStop();
    worker_.join();
    static_cast<void>(DrainRawFrames());
    captured_frames_.Clear();
    lifecycle_.MarkStopped();
  }

  [[nodiscard]] CaptureState State() const noexcept {
    return lifecycle_.State();
  }

  [[nodiscard]] std::string LastError() const noexcept {
    return lifecycle_.LastError();
  }

  [[nodiscard]] CaptureStatistics Statistics() const noexcept {
    CaptureStatistics snapshot;
    std::optional<std::chrono::steady_clock::time_point> latest_time;
    std::array<double, kConversionSampleCount> samples{};
    std::size_t sample_count = 0U;
    {
      std::scoped_lock statistics_lock(statistics_mutex_);
      snapshot = statistics_;
      latest_time = latest_frame_time_;
      sample_count = conversion_samples_ms_.size();
      std::copy(conversion_samples_ms_.begin(),
                conversion_samples_ms_.end(), samples.begin());
    }
    snapshot.queued_frames = raw_frames_.Size();
    if (latest_time.has_value()) {
      snapshot.latest_frame_age =
          std::chrono::duration_cast<std::chrono::milliseconds>(
              std::chrono::steady_clock::now() - *latest_time);
    }
    if (sample_count != 0U) {
      snapshot.conversion_p50_ms = Percentile(samples, sample_count, 0.50);
      snapshot.conversion_p95_ms = Percentile(samples, sample_count, 0.95);
    }
    return snapshot;
  }

  [[nodiscard]] common::FrameSource Source() const { return source_; }

  [[nodiscard]] std::optional<common::Frame> TryGetNextFrame() {
    auto captured = TryGetNextCapturedFrame();
    if (!captured.has_value()) {
      return std::nullopt;
    }
    return std::move(captured->frame);
  }

  [[nodiscard]] std::optional<common::CapturedFrame>
  TryGetNextCapturedFrame() {
    return captured_frames_.Take();
  }

  [[nodiscard]] std::uint64_t CurrentCaptureEpoch() const noexcept {
    return captured_frames_.CurrentCaptureEpoch();
  }

 private:
  [[nodiscard]] std::uint64_t ResetForStart() {
    lifecycle_.Reset();
    next_frame_id_.store(1U);
    static_cast<void>(DrainRawFrames());
    const std::uint64_t capture_epoch =
        captured_frames_.BeginCaptureEpoch();
    {
      std::scoped_lock statistics_lock(statistics_mutex_);
      statistics_ = {};
      statistics_.queue_capacity = queue_capacity_;
      latest_frame_time_.reset();
      conversion_samples_ms_.clear();
    }
    return capture_epoch;
  }

  void JoinExistingWorker() noexcept {
    if (!worker_.joinable()) {
      return;
    }
    RequestWorkerStop();
    worker_.join();
  }

  void WorkerMain(std::promise<StartResult> startup_promise,
                  const std::uint64_t capture_epoch) noexcept {
    bool apartment_initialized = false;
    bool startup_reported = false;
    auto report_startup = [&](StartResult result) {
      if (!startup_reported) {
        startup_promise.set_value(std::move(result));
        startup_reported = true;
      }
    };

    try {
      winrt::init_apartment(winrt::apartment_type::multi_threaded);
      apartment_initialized = true;
      if (!wgc::GraphicsCaptureSession::IsSupported()) {
        throw std::runtime_error(
            "Windows Graphics Capture is not supported by this runtime");
      }

      d3d_ = CreateD3dDevice();
      item_ = CreateItemForWindow(window_);
      pool_size_ = SafePoolSize(item_.Size());
      frame_pool_ = wgc::Direct3D11CaptureFramePool::CreateFreeThreaded(
          d3d_.winrt_device, kPixelFormat, kFramePoolBufferCount, pool_size_);
      session_ = frame_pool_.CreateCaptureSession(item_);

      const std::weak_ptr<Impl> weak_self = weak_from_this();
      frame_arrived_token_ = frame_pool_.FrameArrived(
          [weak_self, capture_epoch](
              const wgc::Direct3D11CaptureFramePool& sender,
              const winrt::Windows::Foundation::IInspectable&) {
            if (const auto self = weak_self.lock()) {
              self->OnFrameArrived(sender, capture_epoch);
            }
          });
      item_closed_token_ = item_.Closed(
          [weak_self, capture_epoch](
              const wgc::GraphicsCaptureItem&,
              const winrt::Windows::Foundation::IInspectable&) {
            if (const auto self = weak_self.lock()) {
              self->OnItemClosed(capture_epoch);
            }
          });

      lifecycle_.Activate(item_.Size().Width > 0 && item_.Size().Height > 0);
      try {
        session_.IsCursorCaptureEnabled(false);
      } catch (...) {
      }
      try {
        session_.IsBorderRequired(false);
      } catch (...) {
      }
      session_.StartCapture();
      report_startup(StartResult{true, {}});

      while (!lifecycle_.IsStopRequested()) {
        std::unique_lock wait_lock(wait_mutex_);
        static_cast<void>(work_available_.wait_for(
            wait_lock, std::chrono::milliseconds{100}, [this] {
              return lifecycle_.IsStopRequested() ||
                     raw_frames_.Size() != 0U;
            }));
        const bool target_window_closed = IsWindow(window_) == FALSE;
        wait_lock.unlock();

        if (lifecycle_.IsStopRequested()) {
          break;
        }
        if (target_window_closed) {
          lifecycle_.MarkClosed();
          work_available_.notify_all();
          break;
        }
        auto frame = raw_frames_.TryPopOldest();
        if (!frame.has_value()) {
          continue;
        }

        try {
          if (!ConvertFrame(*frame)) {
            IncrementDropped();
          }
        } catch (const winrt::hresult_error& error) {
          const auto code = static_cast<std::int32_t>(error.code().value);
          std::string message = NarrowMessage(error.message());
          if (detail::IsUnrecoverableGraphicsError(code)) {
            message = "unrecoverable graphics error: " + message;
          }
          FailCapture(detail::CaptureFailure{
              detail::CaptureFailureStage::FrameConversion, code,
              std::move(message)});
          IncrementDropped();
          break;
        } catch (const std::exception& error) {
          FailCapture(detail::CaptureFailure{
              detail::CaptureFailureStage::FrameConversion, std::nullopt,
              error.what()});
          IncrementDropped();
          break;
        } catch (...) {
          FailCapture(detail::CaptureFailure{
              detail::CaptureFailureStage::FrameConversion, std::nullopt,
              "Unknown worker frame conversion failure"});
          IncrementDropped();
          break;
        }
      }
    } catch (const winrt::hresult_error& error) {
      const std::string message = HResultMessage(error);
      FailCapture(detail::CaptureFailure{
          detail::CaptureFailureStage::Startup,
          static_cast<std::int32_t>(error.code().value),
          NarrowMessage(error.message())});
      report_startup(StartResult{false, message});
    } catch (const std::exception& error) {
      const std::string message = error.what();
      FailCapture(detail::CaptureFailure{
          detail::CaptureFailureStage::Startup, std::nullopt, message});
      report_startup(StartResult{false, message});
    } catch (...) {
      const std::string message = "Unknown capture worker failure";
      FailCapture(detail::CaptureFailure{
          detail::CaptureFailureStage::Startup, std::nullopt, message});
      report_startup(StartResult{false, message});
    }

    lifecycle_.RequestStop();
    CloseCaptureObjects();
    static_cast<void>(DrainRawFrames());
    if (!startup_reported) {
      const std::string message = LastError().empty()
                                      ? "Capture worker exited during startup"
                                      : LastError();
      report_startup(StartResult{false, message});
    }
    if (apartment_initialized) {
      winrt::uninit_apartment();
    }
  }

  void OnFrameArrived(
      const wgc::Direct3D11CaptureFramePool& sender,
      const std::uint64_t callback_epoch) noexcept {
    if (!lifecycle_.IsAcceptingFrames() ||
        !captured_frames_.IsCurrent(callback_epoch)) {
      return;
    }
    try {
      std::scoped_lock callback_lock(callback_mutex_);
      if (!lifecycle_.IsAcceptingFrames() ||
          !captured_frames_.IsCurrent(callback_epoch)) {
        return;
      }
      auto frame = sender.TryGetNextFrame();
      if (!frame) {
        return;
      }
      {
        std::scoped_lock statistics_lock(statistics_mutex_);
        ++statistics_.received;
      }

      std::optional<EpochCaptureFrame> dropped_frame;
      QueuePushResult result = QueuePushResult::Added;
      {
        std::scoped_lock wait_lock(wait_mutex_);
        result = raw_frames_.Push(
            EpochCaptureFrame{std::move(frame), callback_epoch},
            &dropped_frame);
      }
      if (result != QueuePushResult::Added) {
        if (dropped_frame.has_value()) {
          dropped_frame->frame.Close();
        }
        IncrementDropped();
      }
      work_available_.notify_one();
    } catch (const winrt::hresult_error& error) {
      IncrementDropped();
      FailCapture(detail::CaptureFailure{
          detail::CaptureFailureStage::FrameArrival,
          static_cast<std::int32_t>(error.code().value),
          NarrowMessage(error.message())});
    } catch (const std::exception& error) {
      IncrementDropped();
      FailCapture(detail::CaptureFailure{
          detail::CaptureFailureStage::FrameArrival, std::nullopt,
          error.what()});
    } catch (...) {
      IncrementDropped();
      FailCapture(detail::CaptureFailure{
          detail::CaptureFailureStage::FrameArrival, std::nullopt,
          "Unknown FrameArrived failure"});
    }
  }

  void OnItemClosed(const std::uint64_t callback_epoch) noexcept {
    if (!captured_frames_.IsCurrent(callback_epoch)) {
      return;
    }
    lifecycle_.MarkClosed();
    work_available_.notify_all();
  }

  [[nodiscard]] bool ConvertFrame(
      EpochCaptureFrame& epoch_frame) {
    if (!captured_frames_.IsCurrent(epoch_frame.callback_epoch)) {
      try {
        epoch_frame.frame.Close();
      } catch (...) {
      }
      return false;
    }
    auto& captured_frame = epoch_frame.frame;
    CaptureFrameGuard frame_guard(captured_frame);
    const auto conversion_started = std::chrono::steady_clock::now();
    const auto content_size = captured_frame.ContentSize();
    if (content_size.Width <= 0 || content_size.Height <= 0) {
      lifecycle_.MarkPaused();
      return false;
    }

    if (content_size.Width != pool_size_.Width ||
        content_size.Height != pool_size_.Height) {
      if (!lifecycle_.BeginResize()) {
        return false;
      }
      std::scoped_lock callback_lock(callback_mutex_);
      frame_guard.Close();
      const std::size_t stale_frames = DrainRawFrames();
      AddDropped(stale_frames);
      staging_texture_ = nullptr;
      try {
        frame_pool_.Recreate(d3d_.winrt_device, kPixelFormat,
                             kFramePoolBufferCount, content_size);
      } catch (const winrt::hresult_error& error) {
        FailCapture(detail::CaptureFailure{
                        detail::CaptureFailureStage::ResizeRecreate,
                        static_cast<std::int32_t>(error.code().value),
                        NarrowMessage(error.message())},
                    true);
        return false;
      } catch (const std::exception& error) {
        FailCapture(detail::CaptureFailure{
                        detail::CaptureFailureStage::ResizeRecreate,
                        std::nullopt, error.what()},
                    true);
        return false;
      } catch (...) {
        FailCapture(detail::CaptureFailure{
                        detail::CaptureFailureStage::ResizeRecreate,
                        std::nullopt, "Unknown frame-pool Recreate failure"},
                    true);
        return false;
      }
      pool_size_ = content_size;
      lifecycle_.CompleteResize();
      return false;
    }

    const auto surface_access = captured_frame.Surface().as<
        ::Windows::Graphics::DirectX::Direct3D11::
            IDirect3DDxgiInterfaceAccess>();
    winrt::com_ptr<ID3D11Texture2D> source_texture;
    winrt::check_hresult(surface_access->GetInterface(
        __uuidof(ID3D11Texture2D), source_texture.put_void()));

    D3D11_TEXTURE2D_DESC source_description{};
    source_texture->GetDesc(&source_description);
    const auto width = static_cast<UINT>(content_size.Width);
    const auto height = static_cast<UINT>(content_size.Height);
    constexpr auto bytes_per_pixel =
        static_cast<std::uint32_t>(common::Frame::kBytesPerPixel);
    if (width > std::numeric_limits<std::uint32_t>::max() /
                    bytes_per_pixel) {
      return false;
    }
    const auto output_stride =
        static_cast<std::uint32_t>(width) * bytes_per_pixel;
    if (source_description.Width < width || source_description.Height < height ||
        (source_description.Format != DXGI_FORMAT_B8G8R8A8_UNORM &&
         source_description.Format != DXGI_FORMAT_B8G8R8A8_UNORM_SRGB)) {
      return false;
    }

    EnsureStagingTexture(width, height, source_description.Format);
    const D3D11_BOX source_box{0U, 0U, 0U, width, height, 1U};
    d3d_.context->CopySubresourceRegion(staging_texture_.get(), 0U, 0U, 0U,
                                        0U, source_texture.get(), 0U,
                                        &source_box);
    const HRESULT device_status = d3d_.device->GetDeviceRemovedReason();
    if (FAILED(device_status)) {
      winrt::check_hresult(device_status);
    }

    D3D11_MAPPED_SUBRESOURCE mapped{};
    winrt::check_hresult(d3d_.context->Map(
        staging_texture_.get(), 0U, D3D11_MAP_READ, 0U, &mapped));
    if (mapped.pData == nullptr || mapped.RowPitch < output_stride) {
      d3d_.context->Unmap(staging_texture_.get(), 0U);
      return false;
    }

    common::Frame output;
    try {
      output.source = source_;
      output.frame_id = next_frame_id_.fetch_add(1U);
      output.width = width;
      output.height = height;
      output.stride = output_stride;
      const std::size_t output_size =
          static_cast<std::size_t>(output.stride) * output.height;
      output.buffer.resize(output_size);
      for (std::uint32_t row = 0U; row < output.height; ++row) {
        const auto* source_row = static_cast<const std::uint8_t*>(mapped.pData) +
                                 (static_cast<std::size_t>(row) *
                                  mapped.RowPitch);
        auto* destination_row =
            output.buffer.data() +
            (static_cast<std::size_t>(row) * output.stride);
        std::memcpy(destination_row, source_row, output.stride);
      }
    } catch (...) {
      d3d_.context->Unmap(staging_texture_.get(), 0U);
      throw;
    }
    d3d_.context->Unmap(staging_texture_.get(), 0U);

    const auto conversion_completed = std::chrono::steady_clock::now();
    output.timestamps.source_timestamp =
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            captured_frame.SystemRelativeTime());
    output.timestamps.capture_started = conversion_started;
    output.timestamps.capture_completed = conversion_completed;
    output.timestamps.captured_at_utc = std::chrono::system_clock::now();

    if (lifecycle_.IsStopRequested() ||
        !captured_frames_.IsCurrent(epoch_frame.callback_epoch)) {
      return false;
    }
    common::CapturedFrame captured;
    captured.identity = common::FrameIdentity{
        output.source, epoch_frame.callback_epoch, output.frame_id};
    captured.frame = std::move(output);
    const auto publish_result = captured_frames_.Publish(
        epoch_frame.callback_epoch, std::move(captured));
    if (publish_result == detail::WgcFramePublishResult::RejectedEpoch ||
        publish_result == detail::WgcFramePublishResult::InvalidFrame) {
      return false;
    }
    if (publish_result == detail::WgcFramePublishResult::Replaced) {
      IncrementDropped();
    }

    const double conversion_ms =
        std::chrono::duration<double, std::milli>(conversion_completed -
                                                  conversion_started)
            .count();
    {
      std::scoped_lock statistics_lock(statistics_mutex_);
      ++statistics_.converted;
      latest_frame_time_ = conversion_completed;
      conversion_samples_ms_.push_back(conversion_ms);
      if (conversion_samples_ms_.size() > kConversionSampleCount) {
        conversion_samples_ms_.pop_front();
      }
    }
    lifecycle_.MarkRunning();
    return true;
  }

  void EnsureStagingTexture(const UINT width, const UINT height,
                            const DXGI_FORMAT format) {
    if (staging_texture_) {
      D3D11_TEXTURE2D_DESC existing{};
      staging_texture_->GetDesc(&existing);
      if (existing.Width == width && existing.Height == height &&
          existing.Format == format) {
        return;
      }
      staging_texture_ = nullptr;
    }

    D3D11_TEXTURE2D_DESC description{};
    description.Width = width;
    description.Height = height;
    description.MipLevels = 1U;
    description.ArraySize = 1U;
    description.Format = format;
    description.SampleDesc.Count = 1U;
    description.Usage = D3D11_USAGE_STAGING;
    description.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    winrt::check_hresult(
        d3d_.device->CreateTexture2D(&description, nullptr,
                                     staging_texture_.put()));
  }

  void CloseCaptureObjects() noexcept {
    std::scoped_lock callback_lock(callback_mutex_);
    static_cast<void>(DrainRawFrames());
    try {
      if (frame_pool_ && frame_arrived_token_.value != 0) {
        frame_pool_.FrameArrived(frame_arrived_token_);
      }
    } catch (...) {
    }
    frame_arrived_token_ = {};
    try {
      if (item_ && item_closed_token_.value != 0) {
        item_.Closed(item_closed_token_);
      }
    } catch (...) {
    }
    item_closed_token_ = {};
    try {
      if (session_) {
        session_.Close();
      }
    } catch (...) {
    }
    try {
      if (frame_pool_) {
        frame_pool_.Close();
      }
    } catch (...) {
    }
    session_ = nullptr;
    frame_pool_ = nullptr;
    item_ = nullptr;
    staging_texture_ = nullptr;
    d3d_ = {};
  }

  void IncrementDropped() noexcept { AddDropped(1U); }

  void RequestWorkerStop() noexcept {
    lifecycle_.RequestStop();
    work_available_.notify_all();
  }

  [[nodiscard]] std::size_t DrainRawFrames() noexcept {
    std::size_t count = 0U;
    while (auto frame = raw_frames_.TryPopOldest()) {
      try {
        frame->frame.Close();
      } catch (...) {
      }
      ++count;
    }
    return count;
  }

  void AddDropped(const std::size_t count) noexcept {
    std::scoped_lock statistics_lock(statistics_mutex_);
    statistics_.dropped += static_cast<std::uint64_t>(count);
  }

  void FailCapture(detail::CaptureFailure failure,
                   const bool callback_lock_held = false) noexcept {
    std::unique_lock<std::mutex> callback_lock(callback_mutex_,
                                               std::defer_lock);
    if (!callback_lock_held) {
      try {
        callback_lock.lock();
      } catch (...) {
      }
    }

    static_cast<void>(detail::TransitionCaptureToFailed(
        lifecycle_, std::move(failure),
        [this] {
          const std::size_t discarded_frames = DrainRawFrames();
          AddDropped(discarded_frames);
          captured_frames_.Clear();
        },
        [this] { work_available_.notify_all(); }));
  }

  const HWND window_;
  const std::size_t queue_capacity_;
  const common::FrameSource source_;
  BoundedQueue<EpochCaptureFrame> raw_frames_;

  mutable std::mutex lifecycle_mutex_{};
  std::thread worker_{};
  detail::CaptureStateMachine lifecycle_{};
  std::mutex callback_mutex_{};
  std::mutex wait_mutex_{};
  std::condition_variable work_available_{};

  D3dDeviceBundle d3d_{};
  wgc::GraphicsCaptureItem item_{nullptr};
  wgc::Direct3D11CaptureFramePool frame_pool_{nullptr};
  wgc::GraphicsCaptureSession session_{nullptr};
  winrt::event_token frame_arrived_token_{};
  winrt::event_token item_closed_token_{};
  winrt::Windows::Graphics::SizeInt32 pool_size_{};
  winrt::com_ptr<ID3D11Texture2D> staging_texture_{};

  std::atomic<std::uint64_t> next_frame_id_{1U};
  detail::WgcCapturedFrameMailbox captured_frames_{};

  mutable std::mutex statistics_mutex_{};
  CaptureStatistics statistics_{};
  std::optional<std::chrono::steady_clock::time_point> latest_frame_time_{};
  std::deque<double> conversion_samples_ms_{};

};

WindowsGraphicsCaptureSource::WindowsGraphicsCaptureSource(
    const HWND window, const std::size_t queue_capacity)
    : impl_(std::make_shared<Impl>(window, queue_capacity)) {}

WindowsGraphicsCaptureSource::~WindowsGraphicsCaptureSource() {
  if (impl_) {
    impl_->Stop();
  }
}

bool WindowsGraphicsCaptureSource::IsSupported(
    std::string* const reason) noexcept {
  try {
    std::promise<StartResult> promise;
    auto future = promise.get_future();
    std::thread support_thread([promise = std::move(promise)]() mutable {
      bool apartment_initialized = false;
      try {
        winrt::init_apartment(winrt::apartment_type::multi_threaded);
        apartment_initialized = true;
        const bool supported = wgc::GraphicsCaptureSession::IsSupported();
        promise.set_value(StartResult{
            supported,
            supported ? std::string{}
                      : "Windows Graphics Capture is not supported by this "
                        "runtime"});
      } catch (const winrt::hresult_error& error) {
        promise.set_value(StartResult{false, HResultMessage(error)});
      } catch (const std::exception& error) {
        promise.set_value(StartResult{false, error.what()});
      } catch (...) {
        promise.set_value(
            StartResult{false, "Unknown WGC support check failure"});
      }
      if (apartment_initialized) {
        winrt::uninit_apartment();
      }
    });
    const StartResult result = future.get();
    support_thread.join();
    if (reason != nullptr) {
      *reason = result.message;
    }
    return result.succeeded;
  } catch (const std::exception& error) {
    if (reason != nullptr) {
      *reason = error.what();
    }
    return false;
  } catch (...) {
    if (reason != nullptr) {
      *reason = "Unable to run WGC support check";
    }
    return false;
  }
}

bool WindowsGraphicsCaptureSource::Start(std::string* const error) noexcept {
  return impl_->Start(error);
}

void WindowsGraphicsCaptureSource::Stop() noexcept { impl_->Stop(); }

CaptureState WindowsGraphicsCaptureSource::State() const noexcept {
  return impl_->State();
}

std::string WindowsGraphicsCaptureSource::LastError() const noexcept {
  return impl_->LastError();
}

CaptureStatistics WindowsGraphicsCaptureSource::Statistics() const noexcept {
  return impl_->Statistics();
}

common::FrameSource WindowsGraphicsCaptureSource::Source() const {
  return impl_->Source();
}

std::optional<common::Frame>
WindowsGraphicsCaptureSource::TryGetNextFrame() {
  return impl_->TryGetNextFrame();
}

std::optional<common::CapturedFrame>
WindowsGraphicsCaptureSource::TryGetNextCapturedFrame() {
  return impl_->TryGetNextCapturedFrame();
}

std::uint64_t WindowsGraphicsCaptureSource::CurrentCaptureEpoch()
    const noexcept {
  return impl_->CurrentCaptureEpoch();
}

}  // namespace lol_assistant::capture
