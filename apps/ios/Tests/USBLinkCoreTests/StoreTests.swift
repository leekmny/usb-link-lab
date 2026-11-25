import Foundation
import XCTest
@testable import USBLinkCore

final class StoreTests: XCTestCase {
    private var root: URL!
    private var store: LabStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try LabStore(root: root)
    }

    override func tearDownWithError() throws {
        try FileManager.default.removeItem(at: root)
    }

    func testSnapshotFreezesExternalFileAndPreservesEmptyFile() throws {
        let source = root.appendingPathComponent("external.bin")
        let id = UUID()
        try Data([0, 255, 0]).write(to: source)
        let metadata = try store.stageAttachment(
            source, id: id, kind: .file, name: "external.bin", mediaType: "application/octet-stream"
        )
        try Data().write(to: source)
        XCTAssertEqual(metadata.size, 3)
        XCTAssertEqual(try Data(contentsOf: store.outgoingURL(id)), Data([0, 255, 0]))

        let emptyID = UUID()
        let empty = try store.stageAttachment(
            source, id: emptyID, kind: .file, name: "empty.bin", mediaType: "application/octet-stream"
        )
        XCTAssertEqual(empty.size, 0)
        XCTAssertEqual(try Data(contentsOf: store.outgoingURL(emptyID)), Data())
        try store.pruneOutgoing(keeping: [emptyID])
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.outgoingURL(id).path))
    }

    func testReceiveCommitCanBeReloadedAndDiscardDoesNotPublish() throws {
        let savedID = UUID()
        let metadata = try ItemMetadata(
            kind: .file, name: "independent.bin", mediaType: "application/octet-stream", size: 2
        )
        let discarded = try store.beginIncoming(UUID(), metadata: metadata)
        try discarded.append(Data([1]))
        discarded.discard()
        XCTAssertTrue(try store.loadReceived().isEmpty)

        let sink = try store.beginIncoming(savedID, metadata: metadata)
        try sink.append(Data([0, 255]))
        let url = try sink.commit(mode: .hmacSHA256)
        let records = try LabStore(root: root).loadReceived()
        XCTAssertEqual(records.count, 1)
        let record = try XCTUnwrap(records.first)
        XCTAssertEqual(record.id, savedID)
        XCTAssertEqual(record.fileURL, url)
        XCTAssertEqual(record.mode, .hmacSHA256)
        XCTAssertEqual(try Data(contentsOf: url), Data([0, 255]))
    }

    func testInvalidUTF8AndFailedCommitDoNotPublish() throws {
        let text = try ItemMetadata(kind: .text, name: "", mediaType: "text/plain; charset=utf-8", size: 1)
        let invalid = try store.beginIncoming(UUID(), metadata: text)
        try invalid.append(Data([0xff]))
        XCTAssertThrowsError(try invalid.commit(mode: .plaintext))
        XCTAssertTrue(try store.loadReceived().isEmpty)

        let id = UUID()
        let valid = try store.beginIncoming(id, metadata: text)
        try valid.append(Data([0x61]))
        let marker = root.appendingPathComponent("received").appendingPathComponent(id.uuidString)
        try FileManager.default.createDirectory(at: marker, withIntermediateDirectories: false)
        XCTAssertThrowsError(try valid.commit(mode: .aes256GCM))
        XCTAssertFalse(FileManager.default.fileExists(atPath: marker.appendingPathComponent("payload").path))
        try FileManager.default.removeItem(at: marker)
        XCTAssertTrue(try store.loadReceived().isEmpty)
    }

    func testLaunchRetainsReadableHistoryAlongsideDamagedRecords() async throws {
        let ids = [UUID(), UUID(), UUID()]
        let metadata = try ItemMetadata(kind: .file, name: "sample.bin",
                                        mediaType: "application/octet-stream", size: 1)
        var urls: [URL] = []
        for id in ids {
            let sink = try store.beginIncoming(id, metadata: metadata)
            try sink.append(Data([42]))
            urls.append(try sink.commit(mode: .aes256GCM))
        }
        // First launch records all three successful receives in the history journal.
        _ = try USBServer(root: root, secret: nil)
        try FileManager.default.removeItem(at: urls[1])
        let damagedRecord = urls[2].deletingLastPathComponent().appendingPathComponent("record.json")
        let damagedBytes = Data("invalid JSON".utf8)
        try damagedBytes.write(to: damagedRecord)

        let restored = try USBServer(root: root, secret: nil)
        let snapshot = await restored.snapshot()
        XCTAssertEqual(snapshot.records.count, 3)
        let valid = try XCTUnwrap(snapshot.records.first { $0.id == ids[0] })
        XCTAssertEqual(valid.state, .completed)
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(valid.fileURL)), Data([42]))
        for id in ids.dropFirst() {
            let record = try XCTUnwrap(snapshot.records.first { $0.id == id })
            XCTAssertEqual(record.state, .interrupted)
            XCTAssertEqual(record.detail, "storage_error")
            XCTAssertNil(record.fileURL)
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: urls[1].deletingLastPathComponent().path))
        XCTAssertEqual(try Data(contentsOf: damagedRecord), damagedBytes)
        XCTAssertEqual(try Data(contentsOf: urls[2]), Data([42]))
    }

    func testReceivedMessageDeletionRemovesOnlyThatItemAndSurvivesRelaunch() async throws {
        let removedID = UUID()
        let siblingID = UUID()
        let attachmentID = UUID()
        let payload = try commitMessage(removedID)
        let sibling = try commitMessage(siblingID)
        let file = try ItemMetadata(kind: .file, name: "sample.bin",
                                    mediaType: "application/octet-stream", size: 1)
        let attachment = try store.beginIncoming(attachmentID, metadata: file)
        try attachment.append(Data([1]))
        let attached = try attachment.commit(mode: .plaintext)

        let server = try USBServer(root: root, secret: nil)
        do {
            try await server.deleteReceivedMessage(attachmentID)
            XCTFail("Deletion accepted an attachment")
        } catch SessionFault.invalidState { }
        try await server.deleteReceivedMessage(removedID)
        let current = await server.snapshot()
        XCTAssertEqual(Set(current.records.map(\.id)), [siblingID, attachmentID])
        XCTAssertFalse(FileManager.default.fileExists(atPath: payload.deletingLastPathComponent().path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: sibling.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: attached.path))

        let reopened = try USBServer(root: root, secret: nil)
        let persisted = await reopened.snapshot()
        XCTAssertEqual(Set(persisted.records.map(\.id)), [siblingID, attachmentID])
        do {
            try await reopened.deleteReceivedMessage(removedID)
            XCTFail("Deletion accepted a stale ID")
        } catch SessionFault.invalidState { }
    }

    func testRelaunchFinishesPendingMessageDeletion() async throws {
        let id = UUID()
        let content = try commitMessage(id)
        let url = root.appendingPathComponent("queue.json")
        var journal = QueueJournal()
        journal.removingReceived = [id]
        try JSONEncoder().encode(journal).write(to: url, options: .atomic)

        let reopened = try USBServer(root: root, secret: nil)
        let snapshot = await reopened.snapshot()
        XCTAssertTrue(snapshot.records.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: content.deletingLastPathComponent().path))
        let cleared = try JSONDecoder().decode(QueueJournal.self, from: Data(contentsOf: url))
        XCTAssertNil(cleared.removingReceived)
    }

    func testJournalWriteFailureLeavesReceivedMessageInHistory() async throws {
        let id = UUID()
        let content = try commitMessage(id)
        let server = try USBServer(root: root, secret: nil)
        let journal = root.appendingPathComponent("queue.json")
        try FileManager.default.removeItem(at: journal)
        try FileManager.default.createDirectory(at: journal, withIntermediateDirectories: false)

        do {
            try await server.deleteReceivedMessage(id)
            XCTFail("Deletion accepted a failed journal update")
        } catch SessionFault.storage { }

        let snapshot = await server.snapshot()
        XCTAssertEqual(snapshot.records.first?.id, id)
        XCTAssertEqual(try Data(contentsOf: content), Data("test".utf8))
    }

    private func commitMessage(_ id: UUID) throws -> URL {
        let data = Data("test".utf8)
        let metadata = try ItemMetadata(kind: .text, name: "", mediaType: "text/plain; charset=utf-8",
                                        size: UInt64(data.count))
        let sink = try store.beginIncoming(id, metadata: metadata)
        try sink.append(data)
        return try sink.commit(mode: .aes256GCM)
    }

    func testEmptyTextCommitsAndPublicFixtureIsRejectedViaLiveKeychain() throws {
        let metadata = try ItemMetadata(kind: .text, name: "", mediaType: "text/plain; charset=utf-8", size: 0)
        let sink = try store.beginIncoming(UUID(), metadata: metadata)
        let url = try sink.commit(mode: .chacha20Poly1305)
        XCTAssertEqual(try Data(contentsOf: url), Data())
        XCTAssertThrowsError(try PairingKeychain.save(Data(0..<32)))
        XCTAssertEqual(try PairingKeychain.generate().count, 32)
    }
}
