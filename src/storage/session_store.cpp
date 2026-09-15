#include "lol_assistant/storage/session_store.h"

#include <winsqlite/winsqlite3.h>

#include <algorithm>
#include <cmath>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "storage_internal.h"

namespace lol_assistant::storage {
namespace {

constexpr int kCurrentSchemaVersion = 1;

[[nodiscard]] StorageStatus SqlStatus(sqlite3* database,
                                      const StorageErrorCode code,
                                      const int native_code,
                                      const std::string_view context) {
  std::string message{context};
  if (database != nullptr) {
    message.append(": ");
    message.append(sqlite3_errmsg(database));
  }
  return {code, native_code, std::move(message)};
}

[[nodiscard]] StorageStatus Exec(sqlite3* database, const char* sql) {
  char* error_message = nullptr;
  const int result = sqlite3_exec(database, sql, nullptr, nullptr, &error_message);
  if (result == SQLITE_OK) {
    return StorageStatus::Ok();
  }
  std::string message = "SQLite statement failed";
  if (error_message != nullptr) {
    message.append(": ");
    message.append(error_message);
    sqlite3_free(error_message);
  }
  return {StorageErrorCode::SqlError, result, std::move(message)};
}

[[nodiscard]] bool IsNonEmptyUtf8(const std::string_view value) noexcept {
  return !value.empty() && internal::IsValidUtf8(value);
}

[[nodiscard]] bool IsOptionalUtf8(
    const std::optional<std::string>& value) noexcept {
  return !value.has_value() || internal::IsValidUtf8(*value);
}

[[nodiscard]] int BindText(sqlite3_stmt* statement, const int index,
                           const std::string_view value) {
  return sqlite3_bind_text(statement, index, value.data(),
                           static_cast<int>(value.size()), SQLITE_TRANSIENT);
}

[[nodiscard]] int BindOptionalText(
    sqlite3_stmt* statement, const int index,
    const std::optional<std::string>& value) {
  if (!value.has_value()) {
    return sqlite3_bind_null(statement, index);
  }
  return BindText(statement, index, *value);
}

class StatementReset final {
 public:
  explicit StatementReset(sqlite3_stmt* statement) noexcept
      : statement_(statement) {}
  ~StatementReset() {
    sqlite3_reset(statement_);
    sqlite3_clear_bindings(statement_);
  }
  StatementReset(const StatementReset&) = delete;
  StatementReset& operator=(const StatementReset&) = delete;

 private:
  sqlite3_stmt* statement_;
};

[[nodiscard]] StorageStatus Prepare(sqlite3* database, const char* sql,
                                    sqlite3_stmt** statement) {
  const int result = sqlite3_prepare_v2(database, sql, -1, statement, nullptr);
  if (result != SQLITE_OK) {
    return SqlStatus(database, StorageErrorCode::SqlError, result,
                     "failed to prepare SQLite statement");
  }
  return StorageStatus::Ok();
}

[[nodiscard]] StorageStatus ConfigureDatabase(
    sqlite3* database, const std::chrono::milliseconds busy_timeout) {
  const auto bounded_timeout = std::clamp<std::int64_t>(
      busy_timeout.count(), 0, std::numeric_limits<int>::max());
  int result = sqlite3_busy_timeout(database, static_cast<int>(bounded_timeout));
  if (result != SQLITE_OK) {
    return SqlStatus(database, StorageErrorCode::SqlError, result,
                     "failed to set SQLite busy timeout");
  }
  auto status = Exec(database, "PRAGMA foreign_keys=ON;");
  if (!status.IsSuccess()) {
    return status;
  }

  sqlite3_stmt* statement = nullptr;
  status = Prepare(database, "PRAGMA foreign_keys;", &statement);
  if (!status.IsSuccess()) {
    return status;
  }
  result = sqlite3_step(statement);
  const bool foreign_keys_enabled =
      result == SQLITE_ROW && sqlite3_column_int(statement, 0) == 1;
  sqlite3_finalize(statement);
  if (!foreign_keys_enabled) {
    return {StorageErrorCode::SqlError, result,
            "SQLite foreign_keys pragma did not become active"};
  }

  statement = nullptr;
  status = Prepare(database, "PRAGMA journal_mode=WAL;", &statement);
  if (!status.IsSuccess()) {
    return status;
  }
  result = sqlite3_step(statement);
  bool wal_enabled = false;
  if (result == SQLITE_ROW) {
    const auto* mode = sqlite3_column_text(statement, 0);
    wal_enabled = mode != nullptr && std::string_view{
                                       reinterpret_cast<const char*>(mode)} ==
                                       "wal";
  }
  sqlite3_finalize(statement);
  if (!wal_enabled) {
    return {StorageErrorCode::SqlError, result,
            "SQLite journal mode did not become WAL"};
  }
  return StorageStatus::Ok();
}

[[nodiscard]] StorageStatus ApplyMigrations(sqlite3* database) {
  auto status = Exec(database, "BEGIN IMMEDIATE;");
  if (!status.IsSuccess()) {
    return status;
  }

  const auto rollback = [database] { (void)Exec(database, "ROLLBACK;"); };
  status = Exec(database,
                "CREATE TABLE IF NOT EXISTS schema_version("
                "version INTEGER PRIMARY KEY,"
                "applied_at_utc TEXT NOT NULL"
                ");");
  if (!status.IsSuccess()) {
    rollback();
    return status;
  }

  sqlite3_stmt* version_statement = nullptr;
  status = Prepare(database,
                   "SELECT COALESCE(MAX(version), 0) FROM schema_version;",
                   &version_statement);
  if (!status.IsSuccess()) {
    rollback();
    return status;
  }
  const int step_result = sqlite3_step(version_statement);
  const int version = step_result == SQLITE_ROW
                          ? sqlite3_column_int(version_statement, 0)
                          : -1;
  sqlite3_finalize(version_statement);
  if (version < 0) {
    rollback();
    return SqlStatus(database, StorageErrorCode::SqlError, step_result,
                     "failed to read schema version");
  }
  if (version > kCurrentSchemaVersion) {
    rollback();
    return {StorageErrorCode::SchemaTooNew, version,
            "database schema is newer than this SessionStore"};
  }

  if (version == 0) {
    constexpr const char* migration_v1 = R"sql(
CREATE TABLE sessions(
  id TEXT PRIMARY KEY,
  started_at_utc TEXT NOT NULL,
  ended_at_utc TEXT,
  champion TEXT,
  metadata_json TEXT NOT NULL
);
CREATE TABLE augment_offers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  offer_round INTEGER NOT NULL CHECK(offer_round BETWEEN 1 AND 4),
  observed_at_utc TEXT NOT NULL,
  confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
  left_augment_id TEXT NOT NULL,
  center_augment_id TEXT NOT NULL,
  right_augment_id TEXT NOT NULL,
  offer_json TEXT NOT NULL,
  UNIQUE(session_id, offer_round),
  CHECK(left_augment_id <> center_augment_id AND
        left_augment_id <> right_augment_id AND
        center_augment_id <> right_augment_id),
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE TABLE augment_choices(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  offer_id INTEGER NOT NULL,
  offer_round INTEGER NOT NULL CHECK(offer_round BETWEEN 1 AND 4),
  selected_augment_id TEXT,
  confirmed INTEGER NOT NULL CHECK(confirmed IN (0, 1)),
  observed_at_utc TEXT NOT NULL,
  UNIQUE(session_id, offer_round),
  CHECK((confirmed = 0 AND selected_augment_id IS NULL) OR
        (confirmed = 1 AND selected_augment_id IS NOT NULL)),
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY(offer_id) REFERENCES augment_offers(id) ON DELETE CASCADE
);
CREATE TRIGGER augment_choice_must_belong_to_offer
BEFORE INSERT ON augment_choices
WHEN NEW.selected_augment_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM augment_offers AS offer
  WHERE offer.id = NEW.offer_id
    AND offer.session_id = NEW.session_id
    AND offer.offer_round = NEW.offer_round
    AND NEW.selected_augment_id IN (
      offer.left_augment_id, offer.center_augment_id, offer.right_augment_id)
)
BEGIN
  SELECT RAISE(ABORT, 'selected augment is not in the referenced offer');
END;
CREATE TABLE recognition_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  offer_id INTEGER,
  slot INTEGER NOT NULL CHECK(slot BETWEEN 0 AND 3),
  recognition_state INTEGER NOT NULL CHECK(recognition_state BETWEEN 0 AND 2),
  augment_id TEXT,
  display_name TEXT,
  confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
  observed_at_utc TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  CHECK(
    (recognition_state = 0 AND slot = 0 AND augment_id IS NULL AND
     display_name IS NULL) OR
    (recognition_state = 1 AND slot BETWEEN 1 AND 3 AND
     augment_id IS NULL AND display_name IS NULL) OR
    (recognition_state = 2 AND slot BETWEEN 1 AND 3 AND
     augment_id IS NOT NULL)
  ),
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY(offer_id) REFERENCES augment_offers(id) ON DELETE SET NULL
);
CREATE TABLE artifacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  artifact_kind TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  sha256 TEXT,
  created_at_utc TEXT NOT NULL,
  UNIQUE(session_id, relative_path),
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX recognition_results_session_idx
  ON recognition_results(session_id, observed_at_utc);
CREATE INDEX artifacts_session_idx ON artifacts(session_id);
INSERT INTO schema_version(version, applied_at_utc)
VALUES(1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
PRAGMA user_version=1;
)sql";
    status = Exec(database, migration_v1);
    if (!status.IsSuccess()) {
      rollback();
      return status;
    }
  }

  status = Exec(database, "COMMIT;");
  if (!status.IsSuccess()) {
    rollback();
  }
  return status;
}

}  // namespace

struct SessionStore::Impl final {
  sqlite3* database{nullptr};
  std::filesystem::path database_path{};
  std::filesystem::path workspace_root{};
  std::recursive_mutex mutex{};
  bool transaction_active{false};

  sqlite3_stmt* create_session{nullptr};
  sqlite3_stmt* end_session{nullptr};
  sqlite3_stmt* insert_offer{nullptr};
  sqlite3_stmt* insert_choice{nullptr};
  sqlite3_stmt* insert_recognition{nullptr};
  sqlite3_stmt* insert_artifact{nullptr};

  ~Impl() { (void)CloseUnsafe(); }

  [[nodiscard]] StorageStatus PrepareStatements() {
    struct Definition final {
      const char* sql;
      sqlite3_stmt** target;
    };
    const Definition definitions[]{
        {"INSERT INTO sessions(id, started_at_utc, champion, metadata_json) "
         "VALUES(?, ?, ?, ?);",
         &create_session},
        {"UPDATE sessions SET ended_at_utc=? WHERE id=?;", &end_session},
        {"INSERT INTO augment_offers("
         "session_id, offer_round, observed_at_utc, confidence, "
         "left_augment_id, center_augment_id, right_augment_id, offer_json) "
         "VALUES(?, ?, ?, ?, ?, ?, ?, ?);",
         &insert_offer},
        {"INSERT INTO augment_choices("
         "session_id, offer_id, offer_round, selected_augment_id, confirmed, "
         "observed_at_utc) "
         "SELECT ?, offer.id, ?, ?, ?, ? FROM augment_offers AS offer "
         "WHERE offer.session_id=? AND offer.offer_round=?;",
         &insert_choice},
        {"INSERT INTO recognition_results("
         "session_id, offer_id, slot, recognition_state, augment_id, "
         "display_name, confidence, observed_at_utc, raw_json) "
         "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?);",
         &insert_recognition},
        {"INSERT INTO artifacts("
         "session_id, artifact_kind, relative_path, sha256, created_at_utc) "
         "VALUES(?, ?, ?, ?, ?);",
         &insert_artifact},
    };
    for (const auto& definition : definitions) {
      auto status = Prepare(database, definition.sql, definition.target);
      if (!status.IsSuccess()) {
        return status;
      }
    }
    return StorageStatus::Ok();
  }

  [[nodiscard]] StorageStatus Step(sqlite3_stmt* statement,
                                   const std::string_view context,
                                   const bool require_change = false) {
    const int result = sqlite3_step(statement);
    if (result != SQLITE_DONE) {
      return SqlStatus(database, StorageErrorCode::SqlError, result, context);
    }
    if (require_change && sqlite3_changes(database) == 0) {
      return {StorageErrorCode::InvalidArgument, SQLITE_NOTFOUND,
              std::string{context} + ": referenced row was not found"};
    }
    return StorageStatus::Ok();
  }

  [[nodiscard]] StorageStatus CloseUnsafe() {
    if (database == nullptr) {
      return StorageStatus::Ok();
    }
    if (transaction_active) {
      (void)Exec(database, "ROLLBACK;");
      transaction_active = false;
    }
    sqlite3_stmt** statements[]{&create_session,   &end_session,
                               &insert_offer,      &insert_choice,
                               &insert_recognition, &insert_artifact};
    for (auto** statement : statements) {
      if (*statement != nullptr) {
        sqlite3_finalize(*statement);
        *statement = nullptr;
      }
    }
    const int result = sqlite3_close(database);
    if (result != SQLITE_OK) {
      return SqlStatus(database, StorageErrorCode::SqlError, result,
                       "failed to close SQLite database");
    }
    database = nullptr;
    return StorageStatus::Ok();
  }
};

SessionStore::SessionStore(std::unique_ptr<Impl> implementation) noexcept
    : impl_(std::move(implementation)) {}

SessionStore::~SessionStore() {
  if (impl_ != nullptr) {
    (void)Close();
  }
}

SessionStore::SessionStore(SessionStore&&) noexcept = default;
SessionStore& SessionStore::operator=(SessionStore&&) noexcept = default;

StorageStatus SessionStore::Open(
    const std::filesystem::path& database_path,
    const std::filesystem::path& workspace_root,
    std::unique_ptr<SessionStore>& store,
    const std::chrono::milliseconds busy_timeout) {
  store.reset();
  if (busy_timeout.count() < 0) {
    return {StorageErrorCode::InvalidArgument, 0,
            "busy timeout must not be negative"};
  }

  internal::WorkspacePath resolved;
  auto status =
      internal::ResolveWorkspacePath(database_path, workspace_root, resolved);
  if (!status.IsSuccess()) {
    return status;
  }

  auto implementation = std::make_unique<Impl>();
  implementation->database_path = resolved.target;
  implementation->workspace_root = resolved.root;
  const auto utf8_path = internal::PathToUtf8(resolved.target);
  const int open_result = sqlite3_open_v2(
      utf8_path.c_str(), &implementation->database,
      SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX,
      nullptr);
  if (open_result != SQLITE_OK) {
    return SqlStatus(implementation->database, StorageErrorCode::OpenFailed,
                     open_result, "failed to open SQLite database");
  }
  sqlite3_extended_result_codes(implementation->database, 1);

  status = ConfigureDatabase(implementation->database, busy_timeout);
  if (!status.IsSuccess()) {
    return status;
  }
  status = ApplyMigrations(implementation->database);
  if (!status.IsSuccess()) {
    return status;
  }
  status = implementation->PrepareStatements();
  if (!status.IsSuccess()) {
    return status;
  }

  store.reset(new SessionStore(std::move(implementation)));
  return StorageStatus::Ok();
}

StorageStatus SessionStore::CreateSession(const SessionRecord& record) {
  if (impl_ == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  std::scoped_lock lock{impl_->mutex};
  if (impl_->database == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  if (!IsNonEmptyUtf8(record.session_id) ||
      !IsNonEmptyUtf8(record.started_at_utc) ||
      !IsOptionalUtf8(record.champion) ||
      !IsNonEmptyUtf8(record.metadata_json)) {
    return {StorageErrorCode::InvalidArgument, 0,
            "session record contains an empty or invalid UTF-8 field"};
  }

  StatementReset reset{impl_->create_session};
  int result = BindText(impl_->create_session, 1, record.session_id);
  result |= BindText(impl_->create_session, 2, record.started_at_utc);
  result |= BindOptionalText(impl_->create_session, 3, record.champion);
  result |= BindText(impl_->create_session, 4, record.metadata_json);
  if (result != SQLITE_OK) {
    return SqlStatus(impl_->database, StorageErrorCode::SqlError, result,
                     "failed to bind session record");
  }
  return impl_->Step(impl_->create_session, "failed to insert session");
}

StorageStatus SessionStore::EndSession(const SessionEndRecord& record) {
  if (impl_ == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  std::scoped_lock lock{impl_->mutex};
  if (impl_->database == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  if (!IsNonEmptyUtf8(record.session_id) ||
      !IsNonEmptyUtf8(record.ended_at_utc)) {
    return {StorageErrorCode::InvalidArgument, 0,
            "session end record contains an empty or invalid UTF-8 field"};
  }
  StatementReset reset{impl_->end_session};
  int result = BindText(impl_->end_session, 1, record.ended_at_utc);
  result |= BindText(impl_->end_session, 2, record.session_id);
  if (result != SQLITE_OK) {
    return SqlStatus(impl_->database, StorageErrorCode::SqlError, result,
                     "failed to bind session end record");
  }
  return impl_->Step(impl_->end_session, "failed to end session", true);
}

StorageStatus SessionStore::InsertAugmentOffer(
    const AugmentOfferRecord& record) {
  if (impl_ == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  std::scoped_lock lock{impl_->mutex};
  if (impl_->database == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  if (!IsNonEmptyUtf8(record.session_id) || record.offer_round < 1U ||
      record.offer_round > 4U || !IsNonEmptyUtf8(record.observed_at_utc) ||
      !std::isfinite(record.confidence) || record.confidence < 0.0 ||
      record.confidence > 1.0 ||
      !std::all_of(record.augment_ids.begin(), record.augment_ids.end(),
                   IsNonEmptyUtf8) ||
      !IsNonEmptyUtf8(record.offer_json)) {
    return {StorageErrorCode::InvalidArgument, 0,
            "augment offer record violates field constraints"};
  }

  StatementReset reset{impl_->insert_offer};
  int result = BindText(impl_->insert_offer, 1, record.session_id);
  result |= sqlite3_bind_int(impl_->insert_offer, 2,
                             static_cast<int>(record.offer_round));
  result |= BindText(impl_->insert_offer, 3, record.observed_at_utc);
  result |= sqlite3_bind_double(impl_->insert_offer, 4, record.confidence);
  result |= BindText(impl_->insert_offer, 5, record.augment_ids[0]);
  result |= BindText(impl_->insert_offer, 6, record.augment_ids[1]);
  result |= BindText(impl_->insert_offer, 7, record.augment_ids[2]);
  result |= BindText(impl_->insert_offer, 8, record.offer_json);
  if (result != SQLITE_OK) {
    return SqlStatus(impl_->database, StorageErrorCode::SqlError, result,
                     "failed to bind augment offer record");
  }
  return impl_->Step(impl_->insert_offer, "failed to insert augment offer");
}

StorageStatus SessionStore::InsertAugmentChoice(
    const AugmentChoiceRecord& record) {
  if (impl_ == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  std::scoped_lock lock{impl_->mutex};
  if (impl_->database == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  if (!IsNonEmptyUtf8(record.session_id) || record.offer_round < 1U ||
      record.offer_round > 4U || !IsOptionalUtf8(record.selected_augment_id) ||
      (record.confirmed != record.selected_augment_id.has_value()) ||
      !IsNonEmptyUtf8(record.observed_at_utc)) {
    return {StorageErrorCode::InvalidArgument, 0,
            "augment choice record violates confirmation constraints"};
  }

  StatementReset reset{impl_->insert_choice};
  int result = BindText(impl_->insert_choice, 1, record.session_id);
  result |= sqlite3_bind_int(impl_->insert_choice, 2,
                             static_cast<int>(record.offer_round));
  result |= BindOptionalText(impl_->insert_choice, 3,
                             record.selected_augment_id);
  result |= sqlite3_bind_int(impl_->insert_choice, 4,
                             record.confirmed ? 1 : 0);
  result |= BindText(impl_->insert_choice, 5, record.observed_at_utc);
  result |= BindText(impl_->insert_choice, 6, record.session_id);
  result |= sqlite3_bind_int(impl_->insert_choice, 7,
                             static_cast<int>(record.offer_round));
  if (result != SQLITE_OK) {
    return SqlStatus(impl_->database, StorageErrorCode::SqlError, result,
                     "failed to bind augment choice record");
  }
  return impl_->Step(impl_->insert_choice, "failed to insert augment choice",
                     true);
}

StorageStatus SessionStore::InsertRecognitionResult(
    const RecognitionResultRecord& record) {
  if (impl_ == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  std::scoped_lock lock{impl_->mutex};
  if (impl_->database == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  if (!IsNonEmptyUtf8(record.session_id) || record.slot > 3U ||
      record.recognition_state > 2U || !IsOptionalUtf8(record.augment_id) ||
      !IsOptionalUtf8(record.display_name) ||
      !std::isfinite(record.confidence) || record.confidence < 0.0 ||
      record.confidence > 1.0 || !IsNonEmptyUtf8(record.observed_at_utc) ||
      !IsNonEmptyUtf8(record.raw_json)) {
    return {StorageErrorCode::InvalidArgument, 0,
            "recognition result record violates field constraints"};
  }
  const bool is_unknown =
      record.recognition_state == 0U && record.slot == 0U &&
      !record.augment_id.has_value() && !record.display_name.has_value();
  const bool is_not_recognized =
      record.recognition_state == 1U && record.slot >= 1U &&
      record.slot <= 3U && !record.augment_id.has_value() &&
      !record.display_name.has_value();
  const bool is_recognized =
      record.recognition_state == 2U && record.slot >= 1U &&
      record.slot <= 3U && record.augment_id.has_value() &&
      IsNonEmptyUtf8(*record.augment_id) &&
      (!record.display_name.has_value() ||
       IsNonEmptyUtf8(*record.display_name));
  if (!is_unknown && !is_not_recognized && !is_recognized) {
    return {StorageErrorCode::InvalidArgument, 0,
            "recognition state does not match slot and nullable field semantics"};
  }

  StatementReset reset{impl_->insert_recognition};
  int result = BindText(impl_->insert_recognition, 1, record.session_id);
  result |= record.offer_id.has_value()
                ? sqlite3_bind_int64(impl_->insert_recognition, 2,
                                     *record.offer_id)
                : sqlite3_bind_null(impl_->insert_recognition, 2);
  result |= sqlite3_bind_int(impl_->insert_recognition, 3,
                             static_cast<int>(record.slot));
  result |= sqlite3_bind_int(impl_->insert_recognition, 4,
                             static_cast<int>(record.recognition_state));
  result |= BindOptionalText(impl_->insert_recognition, 5, record.augment_id);
  result |= BindOptionalText(impl_->insert_recognition, 6, record.display_name);
  result |= sqlite3_bind_double(impl_->insert_recognition, 7,
                                record.confidence);
  result |= BindText(impl_->insert_recognition, 8, record.observed_at_utc);
  result |= BindText(impl_->insert_recognition, 9, record.raw_json);
  if (result != SQLITE_OK) {
    return SqlStatus(impl_->database, StorageErrorCode::SqlError, result,
                     "failed to bind recognition result record");
  }
  return impl_->Step(impl_->insert_recognition,
                     "failed to insert recognition result");
}

StorageStatus SessionStore::InsertArtifact(const ArtifactRecord& record) {
  if (impl_ == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  std::scoped_lock lock{impl_->mutex};
  if (impl_->database == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  if (!IsNonEmptyUtf8(record.session_id) ||
      !IsNonEmptyUtf8(record.artifact_kind) || !IsOptionalUtf8(record.sha256) ||
      !IsNonEmptyUtf8(record.created_at_utc)) {
    return {StorageErrorCode::InvalidArgument, 0,
            "artifact record contains an empty or invalid UTF-8 field"};
  }
  internal::WorkspacePath resolved;
  auto status = internal::ResolveWorkspacePath(
      record.artifact_path, impl_->workspace_root, resolved);
  if (!status.IsSuccess()) {
    return status;
  }
  const auto relative_path = internal::PathToUtf8(resolved.relative);

  StatementReset reset{impl_->insert_artifact};
  int result = BindText(impl_->insert_artifact, 1, record.session_id);
  result |= BindText(impl_->insert_artifact, 2, record.artifact_kind);
  result |= BindText(impl_->insert_artifact, 3, relative_path);
  result |= BindOptionalText(impl_->insert_artifact, 4, record.sha256);
  result |= BindText(impl_->insert_artifact, 5, record.created_at_utc);
  if (result != SQLITE_OK) {
    return SqlStatus(impl_->database, StorageErrorCode::SqlError, result,
                     "failed to bind artifact record");
  }
  return impl_->Step(impl_->insert_artifact, "failed to insert artifact");
}

StorageStatus SessionStore::RunInTransaction(
    const TransactionOperation& operation) {
  if (impl_ == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  if (!operation) {
    return {StorageErrorCode::InvalidArgument, 0,
            "transaction operation must be callable"};
  }
  std::scoped_lock lock{impl_->mutex};
  if (impl_->database == nullptr) {
    return {StorageErrorCode::Closed, 0, "SessionStore is closed"};
  }
  if (impl_->transaction_active) {
    return {StorageErrorCode::InvalidArgument, 0,
            "nested transactions are not supported"};
  }

  auto status = Exec(impl_->database, "BEGIN IMMEDIATE;");
  if (!status.IsSuccess()) {
    return status;
  }
  impl_->transaction_active = true;
  try {
    status = operation(*this);
  } catch (const std::exception& error) {
    (void)Exec(impl_->database, "ROLLBACK;");
    impl_->transaction_active = false;
    return {StorageErrorCode::TransactionFailed, 0,
            std::string{"transaction operation threw: "} + error.what()};
  } catch (...) {
    (void)Exec(impl_->database, "ROLLBACK;");
    impl_->transaction_active = false;
    return {StorageErrorCode::TransactionFailed, 0,
            "transaction operation threw an unknown exception"};
  }

  if (!status.IsSuccess()) {
    (void)Exec(impl_->database, "ROLLBACK;");
    impl_->transaction_active = false;
    return status;
  }
  status = Exec(impl_->database, "COMMIT;");
  if (!status.IsSuccess()) {
    (void)Exec(impl_->database, "ROLLBACK;");
  }
  impl_->transaction_active = false;
  return status;
}

StorageStatus SessionStore::Close() {
  if (impl_ == nullptr) {
    return StorageStatus::Ok();
  }
  std::scoped_lock lock{impl_->mutex};
  if (impl_->transaction_active) {
    return {StorageErrorCode::InvalidArgument, 0,
            "cannot close SessionStore from inside an active transaction"};
  }
  return impl_->CloseUnsafe();
}

bool SessionStore::IsOpen() const noexcept {
  return impl_ != nullptr && impl_->database != nullptr;
}

const std::filesystem::path& SessionStore::DatabasePath() const noexcept {
  static const std::filesystem::path empty_path;
  return impl_ != nullptr ? impl_->database_path : empty_path;
}

const std::filesystem::path& SessionStore::WorkspaceRoot() const noexcept {
  static const std::filesystem::path empty_path;
  return impl_ != nullptr ? impl_->workspace_root : empty_path;
}

}  // namespace lol_assistant::storage
