#pragma once

#include <cstddef>
#include <deque>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <utility>

namespace lol_assistant::capture {

enum class QueueOverflowPolicy {
  DropNewest,
  DropOldest,
};

enum class QueuePushResult {
  Added,
  DroppedNewest,
  DroppedOldest,
};

template <typename T>
class BoundedQueue final {
 public:
  explicit BoundedQueue(const std::size_t capacity,
                        const QueueOverflowPolicy overflow_policy)
      : capacity_(capacity), overflow_policy_(overflow_policy) {
    if (capacity_ == 0U) {
      throw std::invalid_argument("BoundedQueue capacity must be positive");
    }
  }

  [[nodiscard]] QueuePushResult Push(
      T value, std::optional<T>* const dropped_item = nullptr) {
    std::scoped_lock lock(mutex_);
    if (items_.size() < capacity_) {
      items_.push_back(std::move(value));
      return QueuePushResult::Added;
    }

    if (overflow_policy_ == QueueOverflowPolicy::DropNewest) {
      if (dropped_item != nullptr) {
        *dropped_item = std::move(value);
      }
      return QueuePushResult::DroppedNewest;
    }

    if (dropped_item != nullptr) {
      *dropped_item = std::move(items_.front());
    }
    items_.pop_front();
    items_.push_back(std::move(value));
    return QueuePushResult::DroppedOldest;
  }

  [[nodiscard]] std::optional<T> TryPopOldest() {
    std::scoped_lock lock(mutex_);
    if (items_.empty()) {
      return std::nullopt;
    }
    T value = std::move(items_.front());
    items_.pop_front();
    return value;
  }

  [[nodiscard]] std::optional<T> TryPopNewestAndClear() {
    std::scoped_lock lock(mutex_);
    if (items_.empty()) {
      return std::nullopt;
    }
    T value = std::move(items_.back());
    items_.clear();
    return value;
  }

  [[nodiscard]] std::size_t Clear() {
    std::scoped_lock lock(mutex_);
    const std::size_t count = items_.size();
    items_.clear();
    return count;
  }

  [[nodiscard]] std::size_t Size() const {
    std::scoped_lock lock(mutex_);
    return items_.size();
  }

  [[nodiscard]] std::size_t Capacity() const noexcept { return capacity_; }

 private:
  const std::size_t capacity_;
  const QueueOverflowPolicy overflow_policy_;
  mutable std::mutex mutex_{};
  std::deque<T> items_{};
};

}  // namespace lol_assistant::capture
