#include "lol_assistant/capture/desktop_duplication_source.h"

#include <dxgi.h>

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <limits>
#include <mutex>
#include <sstream>
#include <thread>
#include <utility>

#include "desktop_duplication_backend.h"

namespace lol_assistant::capture {
namespace detail {
namespace {

[[nodiscard]] bool IsDeviceFailure(const HRESULT result) noexcept {
  return result == DXGI_ERROR_DEVICE_REMOVED ||
         result == DXGI_ERROR_DEVICE_RESET ||
         result == DXGI_ERROR_DEVICE_HUNG ||
         result == DXGI_ERROR_DRIVER_INTERNAL_ERROR;
}

[[nodiscard]] bool HasValidBuffer(const DesktopBackendFrame& frame) noexcept {
  if (frame.width == 0U || frame.height == 0U ||
      frame.width > std::numeric_limits<std::uint32_t>::max() /
                        static_cast<std::uint32_t>(common::Frame::kBytesPerPixel)) {
    return false;
  }
  const auto minimum_stride =
      frame.width * static_cast<std::uint32_t>(common::Frame::kBytesPerPixel);
  if (frame.stride < minimum_stride) {
    return false;
  }
  const auto required = static_cast<std::size_t>(frame.stride) * frame.height;
  return required / frame.height == frame.stride &&
         frame.buffer.size() == required;
}

}  // namespace

DesktopDuplicationBackendController::DesktopDuplicationBackendController(
    DesktopDuplicationOptions options,
    std::unique_ptr<IDesktopDuplicationBackend> backend,
    common::FrameSource source)
    : options_(std::move(options)),
      backend_(std::move(backend)),
      frames_(std::clamp(options_.queue_capacity, std::size_t{1U},
                         DesktopDuplicationSource::kMaximumQueueCapacity),
              QueueOverflowPolicy::DropOldest),
      base_source_(std::move(source)) {
  options_.queue_capacity = frames_.Capacity();
  if (options_.acquire_timeout.count() < 0) {
    options_.acquire_timeout = std::chrono::milliseconds{0};
  }
  statistics_.queue_capacity = frames_.Capacity();
}

DesktopDuplicationBackendController::~DesktopDuplicationBackendController() {
  Stop();
}

bool DesktopDuplicationBackendController::Start(
    const DesktopCaptureGeometry& geometry,
    std::string* const error) noexcept {
  std::scoped_lock lock(mutex_);
  if (backend_ == nullptr) {
    state_ = CaptureState::Failed;
    last_error_ = "Desktop Duplication backend is unavailable";
    if (error != nullptr) {
      *error = last_error_;
    }
    return false;
  }
  if (state_ == CaptureState::Running || state_ == CaptureState::Paused) {
    if (error != nullptr) {
      error->clear();
    }
    return true;
  }

  state_ = CaptureState::Starting;
  last_error_.clear();
  geometry_ = geometry;
  if (!geometry.IsReady()) {
    state_ = CaptureState::Paused;
    if (error != nullptr) {
      error->clear();
    }
    return true;
  }
  return InitializeBackend(geometry, true, false, error);
}

bool DesktopDuplicationBackendController::Poll(
    const DesktopCaptureGeometry& geometry) noexcept {
  std::scoped_lock lock(mutex_);
  if (state_ == CaptureState::Stopped || state_ == CaptureState::Stopping ||
      state_ == CaptureState::Failed) {
    return false;
  }

  geometry_ = geometry;
  if (!geometry.IsReady()) {
    ClearQueuedFrames();
    state_ = CaptureState::Paused;
    return true;
  }
  if (active_monitor_ != geometry.monitor) {
    std::string error;
    if (!InitializeBackend(geometry, true, active_monitor_ != nullptr,
                           &error)) {
      return false;
    }
  }

  DesktopAcquireResult acquired =
      backend_->AcquireNextFrame(options_.acquire_timeout, geometry);
  HRESULT release_result = S_OK;
  if (acquired.frame_acquired) {
    ++statistics_.acquired;
    release_result = backend_->ReleaseFrame();
    ++statistics_.released;
  }
  if (FAILED(release_result)) {
    std::ostringstream message;
    message << "ReleaseFrame failed with HRESULT 0x" << std::hex
            << std::uppercase << static_cast<std::uint32_t>(release_result);
    SetError(message.str());
    state_ = CaptureState::Failed;
    return false;
  }

  if (acquired.result == DXGI_ERROR_WAIT_TIMEOUT) {
    ++statistics_.wait_timeouts;
    return true;
  }
  if (FAILED(acquired.result)) {
    if (!acquired.diagnostic.empty()) {
      SetError(acquired.diagnostic);
    }
    if (acquired.result == DXGI_ERROR_ACCESS_LOST ||
        acquired.result == DXGI_ERROR_SESSION_DISCONNECTED ||
        acquired.result == E_ACCESSDENIED ||
        IsDeviceFailure(acquired.result)) {
      return Recover(geometry, acquired.result);
    }
    return false;
  }
  if (!acquired.frame_acquired || !HasValidBuffer(acquired.frame)) {
    SetError("Desktop Duplication returned an invalid CPU frame");
    return false;
  }

  common::Frame frame;
  frame.source = SourceForEpoch(capture_epoch_);
  frame.frame_id = next_frame_id_++;
  frame.width = acquired.frame.width;
  frame.height = acquired.frame.height;
  frame.stride = acquired.frame.stride;
  frame.buffer = std::move(acquired.frame.buffer);
  const auto completed = std::chrono::steady_clock::now();
  frame.timestamps.source_timestamp = acquired.frame.source_timestamp;
  frame.timestamps.capture_started = completed;
  frame.timestamps.capture_completed = completed;
  frame.timestamps.captured_at_utc = std::chrono::system_clock::now();

  common::CapturedFrame captured;
  captured.identity =
      common::FrameIdentity{frame.source, capture_epoch_, frame.frame_id};
  captured.frame = std::move(frame);
  std::optional<common::CapturedFrame> dropped;
  const QueuePushResult push_result = frames_.Push(std::move(captured), &dropped);
  if (push_result != QueuePushResult::Added) {
    ++statistics_.dropped;
  }
  ++statistics_.converted;
  if (acquired.frame.protected_content_masked) {
    ++statistics_.protected_content_frames;
  }
  latest_frame_time_ = completed;
  last_error_.clear();
  state_ = CaptureState::Running;
  return true;
}

void DesktopDuplicationBackendController::Stop() noexcept {
  std::scoped_lock lock(mutex_);
  if (state_ == CaptureState::Stopped) {
    return;
  }
  state_ = CaptureState::Stopping;
  ClearQueuedFrames();
  if (backend_ != nullptr) {
    backend_->Shutdown();
  }
  active_monitor_ = nullptr;
  state_ = CaptureState::Stopped;
}

CaptureState DesktopDuplicationBackendController::State() const noexcept {
  std::scoped_lock lock(mutex_);
  return state_;
}

std::string DesktopDuplicationBackendController::LastError() const noexcept {
  std::scoped_lock lock(mutex_);
  return last_error_;
}

DesktopDuplicationStatistics
DesktopDuplicationBackendController::Statistics() const noexcept {
  std::scoped_lock lock(mutex_);
  DesktopDuplicationStatistics snapshot = statistics_;
  snapshot.queued_frames = frames_.Size();
  snapshot.capture_epoch = capture_epoch_;
  if (latest_frame_time_.has_value()) {
    snapshot.latest_frame_age =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - *latest_frame_time_);
  }
  return snapshot;
}

std::optional<DesktopCaptureGeometry>
DesktopDuplicationBackendController::Geometry() const noexcept {
  std::scoped_lock lock(mutex_);
  return geometry_;
}

std::optional<common::CapturedFrame>
DesktopDuplicationBackendController::TryGetNextFrame() {
  std::scoped_lock lock(mutex_);
  const std::size_t queued = frames_.Size();
  auto result = frames_.TryPopNewestAndClear();
  if (queued > 1U) {
    statistics_.dropped += static_cast<std::uint64_t>(queued - 1U);
  }
  return result;
}

std::uint64_t
DesktopDuplicationBackendController::CurrentCaptureEpoch() const noexcept {
  std::scoped_lock lock(mutex_);
  return capture_epoch_;
}

bool DesktopDuplicationBackendController::InitializeBackend(
    const DesktopCaptureGeometry& geometry, const bool recreate_device,
    const bool is_recovery, std::string* const error) noexcept {
  ClearQueuedFrames();
  const HRESULT result =
      backend_->Initialize(geometry.monitor, recreate_device, error);
  if (FAILED(result)) {
    std::ostringstream message;
    message << "Unable to initialize Desktop Duplication (HRESULT 0x"
            << std::hex << std::uppercase
            << static_cast<std::uint32_t>(result) << ')';
    if (error != nullptr && !error->empty()) {
      message << ": " << *error;
    }
    SetError(message.str());
    state_ = CaptureState::Failed;
    if (error != nullptr) {
      *error = last_error_;
    }
    return false;
  }
  if (is_recovery) {
    if (recreate_device) {
      ++statistics_.device_recreates;
    } else {
      ++statistics_.duplication_recreates;
    }
  }
  active_monitor_ = geometry.monitor;
  geometry_ = geometry;
  ++capture_epoch_;
  statistics_.capture_epoch = capture_epoch_;
  next_frame_id_ = 1U;
  latest_frame_time_.reset();
  last_error_.clear();
  state_ = CaptureState::Running;
  if (error != nullptr) {
    error->clear();
  }
  return true;
}

bool DesktopDuplicationBackendController::Recover(
    const DesktopCaptureGeometry& geometry, const HRESULT failure) noexcept {
  ClearQueuedFrames();
  state_ = CaptureState::Paused;
  bool recreate_device = false;
  if (failure == DXGI_ERROR_ACCESS_LOST) {
    ++statistics_.access_lost;
  } else if (failure == DXGI_ERROR_SESSION_DISCONNECTED ||
             failure == E_ACCESSDENIED) {
    ++statistics_.session_disconnects;
  } else if (IsDeviceFailure(failure)) {
    ++statistics_.device_removed;
    recreate_device = true;
  }
  std::string error;
  return InitializeBackend(geometry, recreate_device, true, &error);
}

void DesktopDuplicationBackendController::ClearQueuedFrames() noexcept {
  statistics_.dropped += static_cast<std::uint64_t>(frames_.Clear());
}

void DesktopDuplicationBackendController::SetError(std::string message) noexcept {
  try {
    last_error_ = std::move(message);
  } catch (...) {
    last_error_ = "Desktop Duplication failure";
  }
}

common::FrameSource DesktopDuplicationBackendController::SourceForEpoch(
    const std::uint64_t epoch) const {
  std::ostringstream id;
  id << base_source_.id << ";epoch:" << epoch;
  return {base_source_.kind, id.str()};
}

}  // namespace detail
namespace {

[[nodiscard]] std::string TargetSourceId(const DesktopCaptureTarget& target) {
  std::ostringstream stream;
  if (target.kind == DesktopCaptureTargetKind::Monitor) {
    stream << "monitor:0x" << std::hex << std::uppercase
           << reinterpret_cast<std::uintptr_t>(target.monitor);
  } else {
    stream << "hwnd:0x" << std::hex << std::uppercase
           << reinterpret_cast<std::uintptr_t>(target.window);
  }
  return stream.str();
}

}  // namespace

class DesktopDuplicationSource::Impl final {
 public:
  Impl(DesktopCaptureTarget target, DesktopDuplicationOptions options)
      : target_(target),
        options_(std::move(options)),
        source_{common::FrameSourceKind::DesktopDuplication,
                TargetSourceId(target)},
        controller_(options_, detail::CreateDxgiDesktopDuplicationBackend(),
                    source_) {}

  ~Impl() { Stop(); }

  [[nodiscard]] bool Start(std::string* const error) noexcept {
    std::scoped_lock lock(lifecycle_mutex_);
    if (worker_.joinable() &&
        (controller_.State() == CaptureState::Running ||
         controller_.State() == CaptureState::Paused)) {
      if (error != nullptr) {
        error->clear();
      }
      return true;
    }
    if (worker_.joinable()) {
      stop_requested_.store(true, std::memory_order_release);
      wake_.notify_all();
      worker_.join();
    }

    std::string geometry_error;
    const DesktopCaptureGeometry geometry = ResolveDesktopCaptureGeometry(
        target_, options_.partial_visibility, &geometry_error);
    if (geometry.status == DesktopGeometryStatus::InvalidTarget ||
        geometry.status == DesktopGeometryStatus::NoOutputs) {
      if (error != nullptr) {
        *error = geometry_error.empty() ? "Invalid desktop capture target"
                                        : geometry_error;
      }
      return false;
    }
    if (!controller_.Start(geometry, error)) {
      return false;
    }

    stop_requested_.store(false, std::memory_order_release);
    try {
      worker_ = std::thread([this] { WorkerMain(); });
    } catch (const std::exception& exception) {
      controller_.Stop();
      if (error != nullptr) {
        *error = exception.what();
      }
      return false;
    } catch (...) {
      controller_.Stop();
      if (error != nullptr) {
        *error = "Unable to create Desktop Duplication worker";
      }
      return false;
    }
    if (error != nullptr) {
      error->clear();
    }
    return true;
  }

  void Stop() noexcept {
    std::scoped_lock lock(lifecycle_mutex_);
    stop_requested_.store(true, std::memory_order_release);
    wake_.notify_all();
    if (worker_.joinable()) {
      worker_.join();
    }
    controller_.Stop();
  }

  [[nodiscard]] CaptureState State() const noexcept {
    return controller_.State();
  }

  [[nodiscard]] std::string LastError() const noexcept {
    return controller_.LastError();
  }

  [[nodiscard]] DesktopDuplicationStatistics Statistics() const noexcept {
    return controller_.Statistics();
  }

  [[nodiscard]] std::optional<DesktopCaptureGeometry> Geometry() const noexcept {
    return controller_.Geometry();
  }

  [[nodiscard]] common::FrameSource Source() const { return source_; }

  [[nodiscard]] std::optional<common::CapturedFrame>
  TryGetNextCapturedFrame() {
    return controller_.TryGetNextFrame();
  }

  [[nodiscard]] std::optional<common::Frame> TryGetNextFrame() {
    auto captured = TryGetNextCapturedFrame();
    if (!captured.has_value()) {
      return std::nullopt;
    }
    return std::move(captured->frame);
  }

  [[nodiscard]] std::uint64_t CurrentCaptureEpoch() const noexcept {
    return controller_.CurrentCaptureEpoch();
  }

 private:
  void WorkerMain() noexcept {
    while (!stop_requested_.load(std::memory_order_acquire)) {
      std::string geometry_error;
      const DesktopCaptureGeometry geometry = ResolveDesktopCaptureGeometry(
          target_, options_.partial_visibility, &geometry_error);
      const bool ok = controller_.Poll(geometry);
      if (!ok && controller_.State() == CaptureState::Failed) {
        break;
      }

      std::chrono::milliseconds wait{0};
      if (!geometry.IsReady()) {
        wait = std::chrono::milliseconds{100};
      } else if (!ok) {
        wait = options_.recovery_backoff;
      }
      if (wait.count() > 0) {
        std::unique_lock wait_lock(wait_mutex_);
        static_cast<void>(wake_.wait_for(wait_lock, wait, [this] {
          return stop_requested_.load(std::memory_order_acquire);
        }));
      }
    }
  }

  DesktopCaptureTarget target_{};
  DesktopDuplicationOptions options_{};
  common::FrameSource source_{};
  detail::DesktopDuplicationBackendController controller_;
  mutable std::mutex lifecycle_mutex_{};
  std::thread worker_{};
  std::atomic<bool> stop_requested_{false};
  std::mutex wait_mutex_{};
  std::condition_variable wake_{};
};

DesktopDuplicationSource::DesktopDuplicationSource(
    const DesktopCaptureTarget target, DesktopDuplicationOptions options)
    : impl_(std::make_unique<Impl>(target, std::move(options))) {}

DesktopDuplicationSource::~DesktopDuplicationSource() = default;

bool DesktopDuplicationSource::IsSupported(
    const DesktopCaptureTarget& target, std::string* const reason) noexcept {
  std::string geometry_error;
  const DesktopCaptureGeometry geometry = ResolveDesktopCaptureGeometry(
      target, PartialVisibilityPolicy::PauseUnlessFullyVisible,
      &geometry_error);
  if (geometry.status == DesktopGeometryStatus::InvalidTarget ||
      geometry.status == DesktopGeometryStatus::NoOutputs) {
    if (reason != nullptr) {
      *reason = geometry_error.empty() ? "Invalid desktop capture target"
                                      : geometry_error;
    }
    return false;
  }
  if (!geometry.IsReady()) {
    if (reason != nullptr) {
      reason->clear();
    }
    return true;
  }

  auto backend = detail::CreateDxgiDesktopDuplicationBackend();
  if (backend == nullptr) {
    if (reason != nullptr) {
      *reason = "Desktop Duplication backend is unavailable";
    }
    return false;
  }
  const HRESULT result = backend->Initialize(geometry.monitor, true, reason);
  backend->Shutdown();
  return SUCCEEDED(result);
}

bool DesktopDuplicationSource::Start(std::string* const error) noexcept {
  return impl_->Start(error);
}

void DesktopDuplicationSource::Stop() noexcept { impl_->Stop(); }

CaptureState DesktopDuplicationSource::State() const noexcept {
  return impl_->State();
}

std::string DesktopDuplicationSource::LastError() const noexcept {
  return impl_->LastError();
}

DesktopDuplicationStatistics DesktopDuplicationSource::Statistics() const
    noexcept {
  return impl_->Statistics();
}

std::optional<DesktopCaptureGeometry> DesktopDuplicationSource::Geometry()
    const noexcept {
  return impl_->Geometry();
}

common::FrameSource DesktopDuplicationSource::Source() const {
  return impl_->Source();
}

std::optional<common::Frame> DesktopDuplicationSource::TryGetNextFrame() {
  return impl_->TryGetNextFrame();
}

std::optional<common::CapturedFrame>
DesktopDuplicationSource::TryGetNextCapturedFrame() {
  return impl_->TryGetNextCapturedFrame();
}

std::uint64_t DesktopDuplicationSource::CurrentCaptureEpoch() const noexcept {
  return impl_->CurrentCaptureEpoch();
}

}  // namespace lol_assistant::capture
