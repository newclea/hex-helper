#include "lol_assistant/storage/async_storage_writer.h"
#include "lol_assistant/storage/jsonl_writer.h"
#include "lol_assistant/storage/session_store.h"

#include <winsqlite/winsqlite3.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using lol_assistant::storage::ArtifactRecord;
using lol_assistant::storage::AsyncStorageOptions;
using lol_assistant::storage::AsyncStorageWriter;
using lol_assistant::storage::AugmentChoiceRecord;
using lol_assistant::storage::AugmentOfferRecord;
using lol_assistant::storage::EnqueueResult;
using lol_assistant::storage::JsonlEvent;
using lol_assistant::storage::JsonlWriter;
using lol_assistant::storage::RecognitionResultRecord;
using lol_assistant::storage::SessionRecord;
using lol_assistant::storage::SessionStore;
using lol_assistant::storage::StorageErrorCode;
using lol_assistant::storage::StoragePriority;
using lol_assistant::storage::StorageStatus;

void Require(const bool condition, const std::string_view message) {
  if (!condition) {
    throw std::runtime_error(std::string{message});
  }
}

void RequireStatus(const StorageStatus& status, const std::string_view context) {
  if (!status.IsSuccess()) {
    throw std::runtime_error(std::string{context} + ": " + status.message +
                             " (native=" +
                             std::to_string(status.native_code) + ")");
  }
}

[[nodiscard]] std::string PathUtf8(const std::filesystem::path& path) {
  const auto bytes = path.generic_u8string();
  return {bytes.begin(), bytes.end()};
}

void RemoveDatabaseFiles(const std::filesystem::path& database_path) {
  std::error_code error;
  (void)std::filesystem::remove(database_path, error);
  error.clear();
  (void)std::filesystem::remove(
      std::filesystem::path{database_path.native() + L"-wal"}, error);
  error.clear();
  (void)std::filesystem::remove(
      std::filesystem::path{database_path.native() + L"-shm"}, error);
}

class RawDatabase final {
 public:
  explicit RawDatabase(const std::filesystem::path& path) {
    const auto utf8_path = PathUtf8(path);
    const int result = sqlite3_open_v2(
        utf8_path.c_str(), &database_,
        SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX, nullptr);
    if (result != SQLITE_OK) {
      const std::string error =
          database_ == nullptr ? "unknown" : sqlite3_errmsg(database_);
      throw std::runtime_error("raw sqlite open failed: " + error);
    }
  }

  ~RawDatabase() {
    if (database_ != nullptr) {
      sqlite3_close(database_);
    }
  }

  RawDatabase(const RawDatabase&) = delete;
  RawDatabase& operator=(const RawDatabase&) = delete;

  void Exec(const std::string_view sql) {
    char* error_message = nullptr;
    const std::string sql_text{sql};
    const int result = sqlite3_exec(database_, sql_text.c_str(), nullptr,
                                    nullptr, &error_message);
    if (result != SQLITE_OK) {
      const std::string message =
          error_message == nullptr ? sqlite3_errmsg(database_) : error_message;
      if (error_message != nullptr) {
        sqlite3_free(error_message);
      }
      throw std::runtime_error("raw sqlite exec failed: " + message);
    }
  }

  [[nodiscard]] std::int64_t ScalarInt(const std::string_view sql) {
    sqlite3_stmt* statement = nullptr;
    const std::string sql_text{sql};
    int result = sqlite3_prepare_v2(database_, sql_text.c_str(), -1, &statement,
                                    nullptr);
    if (result != SQLITE_OK) {
      throw std::runtime_error("raw sqlite prepare failed");
    }
    result = sqlite3_step(statement);
    if (result != SQLITE_ROW) {
      sqlite3_finalize(statement);
      throw std::runtime_error("raw sqlite scalar query returned no row");
    }
    const auto value = sqlite3_column_int64(statement, 0);
    sqlite3_finalize(statement);
    return value;
  }

  [[nodiscard]] std::string ScalarText(const std::string_view sql) {
    sqlite3_stmt* statement = nullptr;
    const std::string sql_text{sql};
    int result = sqlite3_prepare_v2(database_, sql_text.c_str(), -1, &statement,
                                    nullptr);
    if (result != SQLITE_OK) {
      throw std::runtime_error("raw sqlite prepare failed");
    }
    result = sqlite3_step(statement);
    if (result != SQLITE_ROW) {
      sqlite3_finalize(statement);
      throw std::runtime_error("raw sqlite scalar query returned no row");
    }
    const auto* value = sqlite3_column_text(statement, 0);
    const std::string text =
        value == nullptr ? "" : reinterpret_cast<const char*>(value);
    sqlite3_finalize(statement);
    return text;
  }

 private:
  sqlite3* database_{nullptr};
};

[[nodiscard]] SessionRecord Session(const std::string& id) {
  return SessionRecord{id, "2026-08-25T10:00:00Z", "Ahri", "{\"source\":\"test\"}"};
}

[[nodiscard]] AugmentOfferRecord Offer(const std::string& session_id,
                                       const std::uint32_t round) {
  const auto prefix = "r" + std::to_string(round);
  return AugmentOfferRecord{session_id,
                            round,
                            "2026-08-25T10:01:00Z",
                            0.96,
                            {prefix + "_left", prefix + "_center",
                             prefix + "_right"},
                            "{\"kind\":\"offer\"}"};
}

void TestExplicitWorkspacePathBoundary(
    const std::filesystem::path& workspace_root,
    const std::filesystem::path& runtime_directory) {
  std::unique_ptr<SessionStore> store;
  const auto relative_status = SessionStore::Open(
      std::filesystem::path{"relative.db"}, workspace_root, store);
  Require(relative_status.code == StorageErrorCode::InvalidPath,
          "relative database path must be rejected");

  const auto outside_path = workspace_root.parent_path() / "outside-wave2.db";
  const auto outside_status =
      SessionStore::Open(outside_path, workspace_root, store);
  Require(outside_status.code == StorageErrorCode::InvalidPath,
          "database path outside the workspace must be rejected");

  const auto valid_path = runtime_directory / "path_boundary.db";
  RemoveDatabaseFiles(valid_path);
  RequireStatus(SessionStore::Open(valid_path, workspace_root, store),
                "valid workspace database open");
  Require(store->DatabasePath().is_absolute(),
          "resolved database path must remain explicit");
  RequireStatus(store->Close(), "path-boundary database close");
}

void TestSchemaRowsForeignKeysTransactionsAndReopen(
    const std::filesystem::path& workspace_root,
    const std::filesystem::path& runtime_directory) {
  const auto database_path = runtime_directory / "storage_validation.db";
  RemoveDatabaseFiles(database_path);
  std::unique_ptr<SessionStore> store;
  RequireStatus(SessionStore::Open(database_path, workspace_root, store),
                "SessionStore open");
  RequireStatus(store->CreateSession(Session("session-main")),
                "insert main session");
  RequireStatus(store->InsertAugmentOffer(Offer("session-main", 1U)),
                "insert round-one offer");
  RequireStatus(store->InsertAugmentChoice(AugmentChoiceRecord{
                    "session-main", 1U, std::nullopt, false,
                    "2026-08-25T10:02:00Z"}),
                "insert explicit unconfirmed choice");
  RequireStatus(store->InsertAugmentOffer(Offer("session-main", 2U)),
                "insert round-two offer");
  RequireStatus(store->InsertAugmentChoice(AugmentChoiceRecord{
                    "session-main", 2U, "r2_center", true,
                    "2026-08-25T10:03:00Z"}),
                "insert confirmed choice");
  RequireStatus(store->InsertRecognitionResult(RecognitionResultRecord{
                    "session-main", 1, 2U, 2U, "r1_center", "中文增幅",
                    0.96, "2026-08-25T10:01:00Z", "{\"ocr\":true}"}),
                "insert recognition result");
  const auto invalid_unknown =
      store->InsertRecognitionResult(RecognitionResultRecord{
          "session-main", std::nullopt, 1U, 0U, "should_be_null",
          std::nullopt, 0.0, "2026-08-25T10:01:00Z", "{}"});
  Require(invalid_unknown.code == StorageErrorCode::InvalidArgument,
          "UNKNOWN recognition must keep slot and recognized fields unknown");
  RequireStatus(store->InsertArtifact(ArtifactRecord{
                    "session-main", "offer_crop",
                    runtime_directory / "artifacts" / "offer-1.png",
                    std::nullopt, "2026-08-25T10:01:01Z"}),
                "insert workspace artifact");

  const auto escaped_artifact = store->InsertArtifact(ArtifactRecord{
      "session-main", "bad", workspace_root.parent_path() / "escape.png",
      std::nullopt, "2026-08-25T10:01:01Z"});
  Require(escaped_artifact.code == StorageErrorCode::InvalidPath,
          "artifact path escaping workspace must be rejected");

  RequireStatus(store->InsertAugmentOffer(Offer("session-main", 3U)),
                "insert round-three offer");
  const auto illegal_choice = store->InsertAugmentChoice(AugmentChoiceRecord{
      "session-main", 3U, "not_in_round_three", true,
      "2026-08-25T10:04:00Z"});
  Require(illegal_choice.code == StorageErrorCode::SqlError,
          "database trigger must reject choice outside referenced offer");

  const auto foreign_key_failure =
      store->InsertAugmentOffer(Offer("missing-session", 1U));
  Require(foreign_key_failure.code == StorageErrorCode::SqlError,
          "foreign key must reject offer for missing session");

  const auto rollback_status = store->RunInTransaction(
      [](SessionStore& transaction_store) {
        const auto insert =
            transaction_store.CreateSession(Session("rolled-back-session"));
        if (!insert.IsSuccess()) {
          return insert;
        }
        return StorageStatus{StorageErrorCode::InvalidArgument, 0,
                             "intentional rollback"};
      });
  Require(rollback_status.code == StorageErrorCode::InvalidArgument,
          "transaction must return callback failure after rollback");

  RequireStatus(store->RunInTransaction([](SessionStore& transaction_store) {
                  return transaction_store.CreateSession(
                      Session("committed-session"));
                }),
                "committed transaction");
  RequireStatus(store->Close(), "SessionStore close");

  RequireStatus(SessionStore::Open(database_path, workspace_root, store),
                "SessionStore reopen");
  RequireStatus(store->Close(), "SessionStore second close");

  RawDatabase raw{database_path};
  Require(raw.ScalarText("PRAGMA journal_mode;") == "wal",
          "database must persist WAL journal mode");
  Require(raw.ScalarInt("PRAGMA user_version;") == 1,
          "user_version must match migration version");
  Require(raw.ScalarInt("SELECT MAX(version) FROM schema_version;") == 1,
          "schema_version table must record migration");
  Require(raw.ScalarInt(
              "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name "
              "IN ('sessions','augment_offers','augment_choices',"
              "'recognition_results','artifacts');") == 5,
          "all required tables must exist");
  Require(raw.ScalarInt("SELECT COUNT(*) FROM sessions;") == 2,
          "rollback must remove one session while commit persists one");
  Require(raw.ScalarInt(
              "SELECT COUNT(*) FROM sessions WHERE id='rolled-back-session';") ==
              0,
          "rolled-back session must not persist");
  Require(raw.ScalarInt(
              "SELECT COUNT(*) FROM augment_choices WHERE confirmed=0 AND "
              "selected_augment_id IS NULL;") == 1,
          "unconfirmed choice must remain SQL NULL");
  Require(raw.ScalarInt(
              "SELECT COUNT(*) FROM augment_choices WHERE confirmed=1 AND "
              "selected_augment_id='r2_center';") == 1,
          "confirmed choice row mismatch");
  Require(raw.ScalarInt("SELECT COUNT(*) FROM pragma_foreign_key_check;") == 0,
          "foreign_key_check must report no violations");
}

void TestJsonlUtf8EscapingFlushAndClose(
    const std::filesystem::path& workspace_root,
    const std::filesystem::path& runtime_directory) {
  const auto jsonl_path = runtime_directory / "unicode_events.jsonl";
  std::error_code error;
  (void)std::filesystem::remove(jsonl_path, error);

  std::unique_ptr<JsonlWriter> writer;
  RequireStatus(JsonlWriter::Open(jsonl_path, workspace_root, writer),
                "JSONL open");
  JsonlEvent event;
  event.event_type = "增幅选择";
  event.timestamp_utc = "2026-08-25T10:05:00Z";
  event.session_id = "会话-1";
  event.fields.emplace("message", "她说：\"好\"\n下一行\\end");
  event.fields.emplace("confirmed", true);
  event.fields.emplace("unknown", nullptr);
  event.fields.emplace("confidence", 0.975);
  RequireStatus(writer->Write(event), "JSONL UTF-8 event write");

  JsonlEvent invalid = event;
  invalid.fields["message"] = std::string{"\xC3\x28", 2U};
  Require(writer->Write(invalid).code == StorageErrorCode::InvalidUtf8,
          "invalid UTF-8 must be rejected");
  RequireStatus(writer->Flush(), "JSONL flush");

  std::ifstream input{jsonl_path, std::ios::binary};
  const std::string contents{std::istreambuf_iterator<char>{input},
                             std::istreambuf_iterator<char>{}};
  Require(std::count(contents.begin(), contents.end(), '\n') == 1,
          "one event must produce exactly one physical line");
  Require(contents.find("增幅选择") != std::string::npos,
          "Chinese text must remain UTF-8");
  Require(contents.find("\\\"好\\\"") != std::string::npos,
          "quotes must be JSON escaped");
  Require(contents.find("\\n下一行\\\\end") != std::string::npos,
          "newline and backslash must be JSON escaped");

  RequireStatus(writer->Close(), "JSONL close");
  Require(writer->Write(event).code == StorageErrorCode::Closed,
          "write after close must be observable");
}

void TestBoundedQueuePressureAndShutdownDrain(
    const std::filesystem::path& workspace_root,
    const std::filesystem::path& runtime_directory) {
  const auto database_path = runtime_directory / "async_validation.db";
  const auto jsonl_path = runtime_directory / "async_events.jsonl";
  RemoveDatabaseFiles(database_path);
  std::error_code error;
  (void)std::filesystem::remove(jsonl_path, error);

  std::unique_ptr<SessionStore> setup_store;
  RequireStatus(SessionStore::Open(database_path, workspace_root, setup_store),
                "async database setup");
  RequireStatus(setup_store->Close(), "async database setup close");

  AsyncStorageOptions options;
  options.workspace_root = workspace_root;
  options.database_path = database_path;
  options.jsonl_path = jsonl_path;
  options.maximum_queue_size = 3U;
  options.busy_timeout = 3s;
  std::unique_ptr<AsyncStorageWriter> writer;
  RequireStatus(AsyncStorageWriter::Start(options, writer),
                "async writer start");

  RawDatabase blocker{database_path};
  blocker.Exec("PRAGMA busy_timeout=3000; BEGIN IMMEDIATE;");
  Require(writer->TryEnqueue(Session("async-session"),
                             StoragePriority::Critical) ==
              EnqueueResult::Accepted,
          "critical session command must be accepted");
  std::this_thread::sleep_for(100ms);

  const auto make_event = [](const std::int64_t sequence) {
    JsonlEvent event;
    event.event_type = "queue-pressure";
    event.timestamp_utc = "2026-08-25T10:06:00Z";
    event.fields.emplace("sequence", sequence);
    return event;
  };
  for (std::int64_t sequence = 1; sequence <= 3; ++sequence) {
    Require(writer->TryEnqueue(make_event(sequence), StoragePriority::Low) ==
                EnqueueResult::Accepted,
            "low-priority command must fill available queue slot");
  }
  Require(writer->TryEnqueue(make_event(4), StoragePriority::Low) ==
              EnqueueResult::RejectedFull,
          "full queue must reject equal low priority");
  Require(writer->TryEnqueue(make_event(99), StoragePriority::Critical) ==
              EnqueueResult::AcceptedAfterDroppingLowerPriority,
          "critical command must replace queued lower-priority work");

  blocker.Exec("COMMIT;");
  RequireStatus(writer->Shutdown(), "async shutdown drain");
  const auto stats = writer->Stats();
  Require(stats.accepted == 5U, "accepted counter mismatch");
  Require(stats.processed == 4U && stats.failed == 0U,
          "shutdown must process every accepted non-dropped command");
  Require(stats.rejected_full == 1U,
          "full-queue rejection counter mismatch");
  Require(stats.dropped_lower_priority == 1U,
          "lower-priority drop counter mismatch");
  Require(stats.queued == 0U, "shutdown must leave no queued commands");
  Require(stats.accepted ==
              stats.processed + stats.failed + stats.dropped_lower_priority,
          "accepted command accounting must close exactly");
  Require(writer->TryEnqueue(make_event(100), StoragePriority::Low) ==
              EnqueueResult::RejectedClosed,
          "enqueue after shutdown must be rejected");

  RawDatabase raw{database_path};
  Require(raw.ScalarInt(
              "SELECT COUNT(*) FROM sessions WHERE id='async-session';") == 1,
          "shutdown drain must persist blocked session command");
  std::ifstream input{jsonl_path, std::ios::binary};
  const std::string contents{std::istreambuf_iterator<char>{input},
                             std::istreambuf_iterator<char>{}};
  Require(std::count(contents.begin(), contents.end(), '\n') == 3,
          "two retained low events plus one critical event must be drained");
}

}  // namespace

int wmain(const int argument_count, wchar_t* arguments[]) {
  if (argument_count != 3) {
    std::cerr << "usage: storage_tests <workspace-root> <runtime-directory>\n";
    return 2;
  }
  const std::filesystem::path workspace_root{arguments[1]};
  const std::filesystem::path runtime_directory{arguments[2]};
  std::error_code error;
  std::filesystem::create_directories(runtime_directory / "artifacts", error);
  if (error) {
    std::cerr << "failed to create runtime directory: " << error.message()
              << '\n';
    return 2;
  }

  using Test = std::function<void(const std::filesystem::path&,
                                  const std::filesystem::path&)>;
  const std::vector<std::pair<std::string_view, Test>> tests{
      {"explicit workspace path boundary", TestExplicitWorkspacePathBoundary},
      {"schema rows foreign keys transaction reopen",
       TestSchemaRowsForeignKeysTransactionsAndReopen},
      {"JSONL UTF-8 escaping flush close", TestJsonlUtf8EscapingFlushAndClose},
      {"bounded queue pressure and shutdown drain",
       TestBoundedQueuePressureAndShutdownDrain},
  };

  std::size_t passed = 0U;
  for (const auto& [name, test] : tests) {
    try {
      test(workspace_root, runtime_directory);
      ++passed;
      std::cout << "[PASS] " << name << '\n';
    } catch (const std::exception& exception) {
      std::cerr << "[FAIL] " << name << ": " << exception.what() << '\n';
    }
  }
  std::cout << "Storage tests: " << passed << '/' << tests.size()
            << " passed.\n";
  return passed == tests.size() ? 0 : 1;
}
