#pragma once

#include <atomic>
#include <cstdint>
#include <iomanip>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>

namespace lol_assistant::capture {

enum class CaptureState : std::uint8_t {
  Stopped,
  Starting,
  Running,
  Paused,
  Stopping,
  Closed,
  Failed,
};

namespace detail {

enum class CaptureFailureStage : std::uint8_t {
  Startup,
  FrameArrival,
  ResizeRecreate,
  FrameConversion,
};

struct CaptureFailure final {
  CaptureFailureStage stage{CaptureFailureStage::FrameConversion};
  std::optional<std::int32_t> hresult{};
  std::string message{};
};

[[nodiscard]] inline const char* CaptureFailureStageText(
    const CaptureFailureStage stage) noexcept {
  switch (stage) {
    case CaptureFailureStage::Startup:
      return "startup";
    case CaptureFailureStage::FrameArrival:
      return "FrameArrived";
    case CaptureFailureStage::ResizeRecreate:
      return "resize Recreate";
    case CaptureFailureStage::FrameConversion:
      return "worker frame conversion";
  }
  return "unknown stage";
}

[[nodiscard]] inline bool IsUnrecoverableGraphicsError(
    const std::int32_t hresult) noexcept {
  switch (static_cast<std::uint32_t>(hresult)) {
    case 0x887A0005U:  // DXGI_ERROR_DEVICE_REMOVED
    case 0x887A0006U:  // DXGI_ERROR_DEVICE_HUNG
    case 0x887A0007U:  // DXGI_ERROR_DEVICE_RESET
    case 0x887A0020U:  // DXGI_ERROR_DRIVER_INTERNAL_ERROR
    case 0x887A0026U:  // DXGI_ERROR_ACCESS_LOST
    case 0x887A0028U:  // DXGI_ERROR_SESSION_DISCONNECTED
      return true;
    default:
      return false;
  }
}

[[nodiscard]] inline std::string DescribeCaptureFailure(
    const CaptureFailure& failure) {
  std::ostringstream stream;
  stream << "Capture failed during " << CaptureFailureStageText(failure.stage);
  if (failure.hresult.has_value()) {
    stream << " (HRESULT 0x" << std::hex << std::uppercase << std::setw(8)
           << std::setfill('0')
           << static_cast<std::uint32_t>(*failure.hresult) << ')';
  }
  if (!failure.message.empty()) {
    stream << ": " << failure.message;
  }
  return stream.str();
}

class CaptureStateMachine final {
 public:
  CaptureStateMachine() = default;

  CaptureStateMachine(const CaptureStateMachine&) = delete;
  CaptureStateMachine& operator=(const CaptureStateMachine&) = delete;

  void Reset() noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(false, std::memory_order_release);
      last_error_.clear();
      state_.store(CaptureState::Stopped, std::memory_order_release);
    } catch (...) {
      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(false, std::memory_order_release);
      state_.store(CaptureState::Stopped, std::memory_order_release);
    }
  }

  void BeginStarting() noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(false, std::memory_order_release);
      state_.store(CaptureState::Starting, std::memory_order_release);
    } catch (...) {
      accepting_frames_.store(false, std::memory_order_release);
      state_.store(CaptureState::Starting, std::memory_order_release);
    }
  }

  void Activate(const bool has_content) noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      if (state_.load(std::memory_order_relaxed) != CaptureState::Starting ||
          stop_requested_.load(std::memory_order_relaxed)) {
        return;
      }
      accepting_frames_.store(true, std::memory_order_release);
      state_.store(has_content ? CaptureState::Running : CaptureState::Paused,
                   std::memory_order_release);
    } catch (...) {
    }
  }

  [[nodiscard]] bool BeginResize() noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      const CaptureState current = state_.load(std::memory_order_relaxed);
      if ((current != CaptureState::Running &&
           current != CaptureState::Paused) ||
          stop_requested_.load(std::memory_order_relaxed)) {
        return false;
      }
      accepting_frames_.store(false, std::memory_order_release);
      state_.store(CaptureState::Paused, std::memory_order_release);
      return true;
    } catch (...) {
      accepting_frames_.store(false, std::memory_order_release);
      return false;
    }
  }

  void CompleteResize() noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      if (state_.load(std::memory_order_relaxed) != CaptureState::Paused ||
          stop_requested_.load(std::memory_order_relaxed)) {
        return;
      }
      accepting_frames_.store(true, std::memory_order_release);
      state_.store(CaptureState::Running, std::memory_order_release);
    } catch (...) {
    }
  }

  void MarkPaused() noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      const CaptureState current = state_.load(std::memory_order_relaxed);
      if ((current == CaptureState::Running ||
           current == CaptureState::Paused) &&
          !stop_requested_.load(std::memory_order_relaxed)) {
        state_.store(CaptureState::Paused, std::memory_order_release);
      }
    } catch (...) {
    }
  }

  void MarkRunning() noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      const CaptureState current = state_.load(std::memory_order_relaxed);
      if ((current == CaptureState::Running ||
           current == CaptureState::Paused) &&
          accepting_frames_.load(std::memory_order_relaxed) &&
          !stop_requested_.load(std::memory_order_relaxed)) {
        state_.store(CaptureState::Running, std::memory_order_release);
      }
    } catch (...) {
    }
  }

  void MarkClosed() noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(true, std::memory_order_release);
      const CaptureState current = state_.load(std::memory_order_relaxed);
      if (current != CaptureState::Failed &&
          current != CaptureState::Stopping &&
          current != CaptureState::Stopped) {
        state_.store(CaptureState::Closed, std::memory_order_release);
      }
    } catch (...) {
      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(true, std::memory_order_release);
    }
  }

  void BeginStopping() noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(true, std::memory_order_release);
      const CaptureState current = state_.load(std::memory_order_relaxed);
      if (current != CaptureState::Closed &&
          current != CaptureState::Failed &&
          current != CaptureState::Stopped) {
        state_.store(CaptureState::Stopping, std::memory_order_release);
      }
    } catch (...) {
      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(true, std::memory_order_release);
    }
  }

  void RequestStop() noexcept {
    accepting_frames_.store(false, std::memory_order_release);
    stop_requested_.store(true, std::memory_order_release);
  }

  void MarkStopped() noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(true, std::memory_order_release);
      state_.store(CaptureState::Stopped, std::memory_order_release);
    } catch (...) {
      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(true, std::memory_order_release);
      state_.store(CaptureState::Stopped, std::memory_order_release);
    }
  }

  [[nodiscard]] bool Fail(CaptureFailure failure) noexcept {
    std::string description;
    try {
      description = DescribeCaptureFailure(failure);
    } catch (...) {
      description = "Capture failed and its diagnostic could not be formatted";
    }

    try {
      std::scoped_lock lock(transition_mutex_);
      const CaptureState current = state_.load(std::memory_order_relaxed);
      if (current == CaptureState::Failed || current == CaptureState::Closed ||
          current == CaptureState::Stopping ||
          current == CaptureState::Stopped) {
        return false;
      }

      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(true, std::memory_order_release);
      last_error_ = std::move(description);
      state_.store(CaptureState::Failed, std::memory_order_release);
      return true;
    } catch (...) {
      accepting_frames_.store(false, std::memory_order_release);
      stop_requested_.store(true, std::memory_order_release);
      state_.store(CaptureState::Failed, std::memory_order_release);
      return true;
    }
  }

  void SetDiagnostic(std::string message) noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      if (state_.load(std::memory_order_relaxed) != CaptureState::Failed) {
        last_error_ = std::move(message);
      }
    } catch (...) {
    }
  }

  [[nodiscard]] CaptureState State() const noexcept {
    return state_.load(std::memory_order_acquire);
  }

  [[nodiscard]] bool IsAcceptingFrames() const noexcept {
    return accepting_frames_.load(std::memory_order_acquire);
  }

  [[nodiscard]] bool IsStopRequested() const noexcept {
    return stop_requested_.load(std::memory_order_acquire);
  }

  [[nodiscard]] std::string LastError() const noexcept {
    try {
      std::scoped_lock lock(transition_mutex_);
      if (last_error_.empty() &&
          state_.load(std::memory_order_relaxed) == CaptureState::Failed) {
        return "Capture failed without an available diagnostic";
      }
      return last_error_;
    } catch (...) {
      return "Capture error unavailable";
    }
  }

 private:
  mutable std::mutex transition_mutex_{};
  std::atomic<CaptureState> state_{CaptureState::Stopped};
  std::atomic<bool> accepting_frames_{false};
  std::atomic<bool> stop_requested_{false};
  std::string last_error_{};
};

template <typename Cleanup, typename Wake>
[[nodiscard]] bool TransitionCaptureToFailed(
    CaptureStateMachine& state_machine, CaptureFailure failure,
    Cleanup&& cleanup, Wake&& wake) noexcept {
  const bool transitioned = state_machine.Fail(std::move(failure));
  if (!transitioned) {
    return false;
  }
  try {
    std::forward<Cleanup>(cleanup)();
  } catch (...) {
  }
  try {
    std::forward<Wake>(wake)();
  } catch (...) {
  }
  return true;
}

}  // namespace detail
}  // namespace lol_assistant::capture
