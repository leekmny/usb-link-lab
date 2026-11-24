import Darwin
import Foundation

enum StoreFailure: Error {
    case insufficientCapacity
    case invalidSource
    case sourceChanged
    case duplicateItem
    case incompleteItem
    case invalidText
    case corruptRecord
}

struct LabStore: Sendable {
    private let outgoing: URL
    private let receiving: URL
    private let received: URL

    init(root: URL) throws {
        try Self.makePrivateDirectory(root)
        let canonicalRoot = root.standardizedFileURL.resolvingSymlinksInPath()
        outgoing = canonicalRoot.appendingPathComponent("outgoing", isDirectory: true)
        receiving = canonicalRoot.appendingPathComponent("receiving", isDirectory: true)
        received = canonicalRoot.appendingPathComponent("received", isDirectory: true)
        var privateRoot = canonicalRoot
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try privateRoot.setResourceValues(values)
        for directory in [outgoing, receiving, received] {
            try Self.makePrivateDirectory(directory)
        }
        for abandoned in try Self.children(of: receiving) {
            if UUID(uuidString: abandoned.lastPathComponent) != nil {
                try FileManager.default.removeItem(at: abandoned)
            }
        }
    }

    func stageText(_ text: String, id: UUID) throws -> ItemMetadata {
        let count = UInt64(text.utf8.count)
        let metadata = try ItemMetadata(kind: .text, name: "",
                                        mediaType: "text/plain; charset=utf-8", size: count)
        try Self.checkCapacity(count, at: outgoing)
        try Self.publishFile(to: outgoingURL(id)) { handle in
            try handle.write(contentsOf: Data(text.utf8))
        }
        return metadata
    }

    func stageAttachment(
        _ source: URL, id: UUID, kind: ItemKind, name: String, mediaType: String
    ) throws -> ItemMetadata {
        guard source.isFileURL, kind != .text else { throw StoreFailure.invalidSource }
        let scoped = source.startAccessingSecurityScopedResource()
        defer { if scoped { source.stopAccessingSecurityScopedResource() } }

        let coordinator = NSFileCoordinator(filePresenter: nil)
        var result: Result<ItemMetadata, Error>?
        var coordinationError: NSError?
        coordinator.coordinate(readingItemAt: source, options: .withoutChanges, error: &coordinationError) { adjusted in
            result = Result {
                try snapshot(adjusted, id: id, kind: kind, name: name, mediaType: mediaType)
            }
        }
        if let coordinationError { throw coordinationError }
        guard let result else { throw StoreFailure.invalidSource }
        return try result.get()
    }

    func outgoingURL(_ id: UUID) -> URL {
        outgoing.appendingPathComponent(id.uuidString, isDirectory: false)
    }

    func removeOutgoing(_ id: UUID) throws {
        try Self.removeIfPresent(outgoingURL(id))
    }

    func pruneOutgoing(keeping ids: Set<UUID>) throws {
        for url in try Self.children(of: outgoing) {
            if let id = UUID(uuidString: url.lastPathComponent), !ids.contains(id) {
                try FileManager.default.removeItem(at: url)
            } else if url.pathExtension == "part", UUID(uuidString: url.deletingPathExtension().lastPathComponent) != nil {
                try FileManager.default.removeItem(at: url)
            }
        }
    }

    func beginIncoming(_ id: UUID, metadata: ItemMetadata) throws -> IncomingSink {
        try Self.checkCapacity(metadata.size + 4096, at: receiving)
        let pending = receiving.appendingPathComponent(id.uuidString, isDirectory: true)
        let final = received.appendingPathComponent(id.uuidString, isDirectory: true)
        guard !FileManager.default.fileExists(atPath: pending.path),
              !FileManager.default.fileExists(atPath: final.path) else { throw StoreFailure.duplicateItem }
        do {
            try Self.makePrivateDirectory(pending)
            return try IncomingSink(id: id, pending: pending, final: final, metadata: metadata)
        } catch {
            try? Self.removeIfPresent(pending)
            throw error
        }
    }

    func loadReceived() throws -> [TransferRecord] {
        let records: [(Date, TransferRecord)] = try Self.children(of: received).compactMap { directory in
            guard let id = UUID(uuidString: directory.lastPathComponent) else { return nil }
            let item = received.appendingPathComponent(id.uuidString, isDirectory: true)
            let recordURL = item.appendingPathComponent("record.json")
            let content = item.appendingPathComponent("payload")
            let recordSize = try Self.regularFileSize(recordURL)
            guard recordSize <= 8192 else { throw StoreFailure.corruptRecord }
            let saved = try JSONDecoder().decode(SavedItem.self, from: Data(contentsOf: recordURL))
            guard saved.id == id, try Self.regularFileSize(content) == saved.metadata.size else {
                throw StoreFailure.corruptRecord
            }
            let record = TransferRecord(
                id: id, direction: .incoming, mode: saved.mode, metadata: saved.metadata,
                state: .completed, bytes: saved.metadata.size, detail: "", fileURL: content
            )
            return (saved.date, record)
        }
        return records.sorted { $0.0 > $1.0 }.map(\.1)
    }

    private func snapshot(
        _ source: URL, id: UUID, kind: ItemKind, name: String, mediaType: String
    ) throws -> ItemMetadata {
        let values = try source.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true else { throw StoreFailure.invalidSource }
        let reader = try FileHandle(forReadingFrom: source)
        defer { try? reader.close() }
        let before = try FileStamp(reader)
        let size = before.size
        let metadata = try ItemMetadata(kind: kind, name: name, mediaType: mediaType, size: size)
        try Self.checkCapacity(size, at: outgoing)

        try Self.publishFile(to: outgoingURL(id)) { writer in
            var remaining = size
            while remaining > 0 {
                try Task.checkCancellation()
                let count = Int(min(remaining, 65_536))
                guard let block = try reader.read(upToCount: count), !block.isEmpty else {
                    throw StoreFailure.sourceChanged
                }
                try writer.write(contentsOf: block)
                remaining -= UInt64(block.count)
            }
            // Both stamps come from the open descriptor, avoiding stale per-URL resource caches.
            guard try reader.read(upToCount: 1)?.isEmpty != false, try FileStamp(reader) == before else {
                throw StoreFailure.sourceChanged
            }
            try Task.checkCancellation()
        }
        return metadata
    }

    private struct FileStamp: Equatable {
        let size: UInt64
        let modifiedSeconds: Int
        let modifiedNanoseconds: Int
        let changedSeconds: Int
        let changedNanoseconds: Int

        init(_ file: FileHandle) throws {
            var info = stat()
            guard fstat(file.fileDescriptor, &info) == 0, info.st_size >= 0,
                  info.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG) else { throw StoreFailure.invalidSource }
            size = UInt64(info.st_size)
            modifiedSeconds = info.st_mtimespec.tv_sec
            modifiedNanoseconds = info.st_mtimespec.tv_nsec
            changedSeconds = info.st_ctimespec.tv_sec
            changedNanoseconds = info.st_ctimespec.tv_nsec
        }
    }

    fileprivate static func publishFile(to target: URL, write: (FileHandle) throws -> Void) throws {
        let temporary = target.deletingLastPathComponent().appendingPathComponent(UUID().uuidString + ".part")
        defer { try? removeIfPresent(temporary) }
        let handle = try newWriter(temporary)
        do {
            try write(handle)
            try handle.synchronize()
            try handle.close()
            try FileManager.default.moveItem(at: temporary, to: target)
        } catch {
            try? handle.close()
            throw error
        }
    }

    fileprivate static func newWriter(_ url: URL) throws -> FileHandle {
        let created = FileManager.default.createFile(atPath: url.path, contents: nil, attributes: fileAttributes)
        guard created else { throw CocoaError(.fileWriteUnknown) }
        return try FileHandle(forWritingTo: url)
    }

    fileprivate static func removeIfPresent(_ url: URL) throws {
        do {
            try FileManager.default.removeItem(at: url)
        } catch let error as CocoaError where error.code == .fileNoSuchFile {
            return
        }
    }

    private static func checkCapacity(_ required: UInt64, at url: URL) throws {
        let attributes = try FileManager.default.attributesOfFileSystem(forPath: url.path)
        if let available = attributes[.systemFreeSize] as? NSNumber, available.uint64Value < required {
            throw StoreFailure.insufficientCapacity
        }
    }

    private static func regularFileSize(_ url: URL) throws -> UInt64 {
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              let size = values.fileSize, size >= 0 else { throw StoreFailure.corruptRecord }
        return UInt64(size)
    }

    private static func children(of directory: URL) throws -> [URL] {
        try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
    }

    private static func makePrivateDirectory(_ url: URL) throws {
        var attributes = fileAttributes
        attributes[.posixPermissions] = 0o700
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true, attributes: attributes)
    }

    private static var fileAttributes: [FileAttributeKey: Any] {
        var attributes: [FileAttributeKey: Any] = [.posixPermissions: 0o600]
        #if os(iOS)
        attributes[.protectionKey] = FileProtectionType.complete
        #endif
        return attributes
    }
}

private struct SavedItem: Codable {
    let id: UUID
    let mode: ProtectionMode
    let metadata: ItemMetadata
    let date: Date
}

final class IncomingSink {
    private let id: UUID
    private let pending: URL
    private let final: URL
    private let metadata: ItemMetadata
    private var handle: FileHandle?
    private var written: UInt64 = 0
    private var published = false

    private var payload: URL { pending.appendingPathComponent("payload") }

    fileprivate init(id: UUID, pending: URL, final: URL, metadata: ItemMetadata) throws {
        self.id = id
        self.pending = pending
        self.final = final
        self.metadata = metadata
        handle = try LabStore.newWriter(payload)
    }

    func append(_ bytes: Data) throws {
        guard let handle, UInt64(bytes.count) <= metadata.size - written else {
            throw StoreFailure.incompleteItem
        }
        try handle.write(contentsOf: bytes)
        written += UInt64(bytes.count)
    }

    func commit(mode: ProtectionMode) throws -> URL {
        do {
            guard let handle, written == metadata.size else { throw StoreFailure.incompleteItem }
            try handle.synchronize()
            try handle.close()
            self.handle = nil
            if metadata.kind == .text {
                let data = try Data(contentsOf: payload)
                guard String(data: data, encoding: .utf8) != nil else { throw StoreFailure.invalidText }
            }
            let saved = SavedItem(id: id, mode: mode, metadata: metadata, date: Date())
            let record = try JSONEncoder().encode(saved)
            try LabStore.publishFile(to: pending.appendingPathComponent("record.json")) { writer in
                try writer.write(contentsOf: record)
            }
            // Both files now exist on the same volume. A single rename publishes the item and its metadata together.
            try FileManager.default.moveItem(at: pending, to: final)
            published = true
            return final.appendingPathComponent("payload")
        } catch {
            discard()
            throw error
        }
    }

    func discard() {
        if let handle { try? handle.close() }
        handle = nil
        if !published { try? LabStore.removeIfPresent(pending) }
    }

    deinit { discard() }
}
