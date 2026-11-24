import XCTest
import Foundation
import CryptoKit
@testable import USBLinkCore

// A test-only ordered duplex stream. Short reads split headers, tags, and content.
// A bounded wait ensures a stalled state machine reports a test failure.
private actor BytePipe {
    var bytes = Data()
    var closed = false
    var reads = 0

    func put(_ data: Data) throws {
        guard !closed else { throw SessionFault.closed }
        bytes.append(data)
    }

    func read(_ count: Int) async throws -> Data {
        let deadline = ContinuousClock.now.advanced(by: .seconds(6))
        while bytes.isEmpty {
            guard !closed else { throw SessionFault.closed }
            guard ContinuousClock.now < deadline else { throw SessionFault.timeout }
            try await Task.sleep(for: .milliseconds(1))
        }
        let part = Data(bytes.prefix(min(count, 3071)))
        bytes.removeFirst(part.count)
        reads += 1
        return part
    }

    func finish() { closed = true }
}

private final class MemoryStream: SessionStream, @unchecked Sendable {
    let input: BytePipe
    let output: BytePipe
    let closeOnRequest: Bool

    init(_ input: BytePipe, _ output: BytePipe, closeOnRequest: Bool = true) {
        self.input = input
        self.output = output
        self.closeOnRequest = closeOnRequest
    }
    func read(upTo count: Int) async throws -> Data { try await input.read(count) }
    func write(_ bytes: Data) async throws { try await output.put(bytes) }
    func close() {
        if closeOnRequest { Task { await input.finish(); await output.finish() } }
    }
}

private final class TestClient {
    let stream: MemoryStream
    var keys: (c2s: DirectionKeys, s2c: DirectionKeys)
    var sent: UInt64 = 0
    var received: UInt64 = 0
    static let psk = Data(repeating: 0x8d, count: 32)
    static let zero = Data(repeating: 0, count: 16)

    init(server: USBServer, deferServerClosure: Bool = false) async throws {
        let toServer = BytePipe(), toClient = BytePipe()
        stream = MemoryStream(toClient, toServer)
        await server.accept(MemoryStream(toServer, toClient, closeOnRequest: !deferServerClosure))
        let ch = try Wire.makeClientHello(psk: Self.psk, nonce: randomBytes(32))
        try await stream.write(ch)
        let sh = try await stream.exact(73)
        let cf = try Wire.makeClientFinish(clientHello: ch, serverHello: sh, psk: Self.psk)
        keys = try Wire.sessionKeys(clientHello: ch, serverHello: sh, clientFinish: cf, psk: Self.psk)
        try await stream.write(cf)
    }

    func send(_ type: FrameType, _ mode: ProtectionMode, _ id: Data, _ body: Data,
              index: UInt64 = 0) async throws {
        let header = FrameHeader(type: type, mode: mode, sequence: sent, itemID: id, index: index, length: body.count)
        let packet = try Wire.encode(header, body: body, keys: keys.c2s)
        try await stream.write(packet)
        sent += 1
    }

    func next() async throws -> (FrameHeader, Data) {
        let fixed = try await stream.exact(76)
        let header = try Wire.decodeHeader(fixed, keys: keys.s2c, expectedSequence: received)
        let bytes = try await stream.exact(Wire.wireBodyLength(header))
        let body = try Wire.decodeBody(bytes, prefixAndHeaderAndMAC: fixed, header: header, keys: keys.s2c)
        received += 1
        return (header, body)
    }

    func open(_ id: Data, mode: ProtectionMode) async throws {
        try await send(.open, mode, id, WireBody.open(mode))
        let (ack, body) = try await next()
        XCTAssertEqual(ack.type, .ack)
        XCTAssertEqual(ack.itemID, id)
        XCTAssertEqual(ack.mode, mode)
        XCTAssertEqual(try WireBody.decodeAck(body), .ready)
    }

    func end(_ id: Data, mode: ProtectionMode, bytes: Data, chunks: UInt64) async throws {
        let body = try WireBody.end(bytes: UInt64(bytes.count), chunks: chunks,
                                    sha256: digestString(SHA256.hash(data: bytes)))
        try await send(.end, mode, id, body, index: chunks)
    }
}

private extension USBServer {
    func exhaustTestBudget() { session?.sendBudget.bytes = SessionBudget.limit - 100 }
    func expirePartialRead() {
        if let session, session.frameDeadline != nil {
            session.frameDeadline = .now.advanced(by: .seconds(-1))
            armTimeout(session)
        }
    }
    func presetACKDeadline() async throws -> ContinuousClock.Instant? {
        guard let session, let outgoing = session.outgoing else { return nil }
        try await session.writerTail?.value
        outgoing.deadline = .now.advanced(by: .seconds(20))
        armTimeout(session)
        return outgoing.deadline
    }
    func seedPeerIDsAtBoundary() {
        guard let session else { return }
        for value in 1...4095 {
            let bytes = [UInt8(value >> 8), UInt8(value & 255)]
            session.peerIDs.insert(Data(repeating: 0, count: 14) + Data(bytes))
        }
        session.peerCompletions = 4095
    }
    func deadlines() -> (ContinuousClock.Instant?, ContinuousClock.Instant?) {
        (session?.frameDeadline, session?.outgoing?.deadline)
    }
}

final class SessionTests: XCTestCase {
    private func server(in dir: URL = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)) async throws -> USBServer {
        addTeardownBlock { try? FileManager.default.removeItem(at: dir) }
        let s = try USBServer(root: dir, secret: TestClient.psk)
        await s.setForeground(true)
        return s
    }

    @discardableResult
    private func wait(_ server: USBServer, until predicate: (LabSnapshot) -> Bool) async throws -> LabSnapshot {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline {
            let value = await server.snapshot()
            if predicate(value) { return value }
            try await Task.sleep(for: .milliseconds(3))
        }
        XCTFail("Session did not reach expected state")
        throw SessionFault.timeout
    }

    private func file(_ size: Int) throws -> ItemMetadata {
        try ItemMetadata(kind: .file, name: "data.bin", mediaType: "application/octet-stream", size: UInt64(size))
    }

    func testQueueLimitAndPairingTargetSurviveRelaunch() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let initial = try USBServer(root: root, secret: TestClient.psk)
        for i in 0..<32 { try await initial.enqueueText("안녕 \(i)", mode: ProtectionMode.allCases[i % 4]) }
        do { try await initial.enqueueText("33", mode: .plaintext); XCTFail("Queue overflow accepted") }
        catch SessionFault.queueFull { }
        let reopened = try USBServer(root: root, secret: Data(repeating: 2, count: 32))
        await reopened.setForeground(true)
        let rows = await reopened.snapshot()
        XCTAssertTrue(rows.paused)
        XCTAssertEqual(rows.records.count, 32)
        XCTAssertTrue(rows.records.allSatisfy { $0.detail == "pairing_changed" })
        XCTAssertEqual(rows.records.map(\.mode), (0..<32).map { ProtectionMode.allCases[$0 % 4] })
    }

    func testBudgetBoundaries() throws {
        var b = SessionBudget()
        XCTAssertTrue(b.canAdmit(size: 0, localIDs: 4095, peerCompletions: 0))
        XCTAssertFalse(b.canAdmit(size: 0, localIDs: 4096, peerCompletions: 0))
        b.bytes = SessionBudget.limit - (6 + 2 * 4096) * 4096 - 11
        XCTAssertTrue(b.canAdmit(size: 11, localIDs: 0, peerCompletions: 0))
        XCTAssertFalse(b.canAdmit(size: 12, localIDs: 0, peerCompletions: 0))
        b.frames = SessionBudget.limit - 7
        XCTAssertTrue(b.canAdmit(size: 1, localIDs: 0, peerCompletions: 4096))
        XCTAssertFalse(b.canAdmit(size: 65537, localIDs: 0, peerCompletions: 4096))
        b.frames = SessionBudget.limit - 1
        b.bytes = SessionBudget.limit - 1
        try b.charge(1)
        XCTAssertThrowsError(try b.charge(0))
    }

    func testBoundarySizesAndSplitUTF8CommitOnlyAfterEnd() async throws {
        let s = try await server()
        let c = try await TestClient(server: s)
        for (i, size) in [0, 1, 65535, 65536, 65537, 65538].enumerated() {
            let mode = ProtectionMode.allCases[i % 4]
            let id = Data(repeating: UInt8(i + 1), count: 16)
            try await c.open(id, mode: mode)
            let splitText = size == 65538
            let bytes = splitText ? Data(repeating: 65, count: 65535) + Data("한".utf8) : Data(repeating: 0, count: size)
            let meta = try splitText
                ? ItemMetadata(kind: .text, name: "", mediaType: "text/plain; charset=utf-8", size: UInt64(size))
                : file(size)
            try await c.send(.meta, mode, id, meta.encode())
            var chunks: UInt64 = 0
            for start in stride(from: 0, to: size, by: 65536) {
                try await c.send(.data, mode, id, bytes.subdata(in: start..<min(start + 65536, size)), index: chunks)
                chunks += 1
            }
            try await c.end(id, mode: mode, bytes: bytes, chunks: chunks)
            let (_, ack) = try await c.next()
            XCTAssertEqual(try WireBody.decodeAck(ack), .complete)
            let snapshot = await s.snapshot()
            let row = try XCTUnwrap(snapshot.records.last)
            XCTAssertEqual(row.mode, mode)
            XCTAssertEqual(row.state, .completed)
            XCTAssertEqual(try Data(contentsOf: XCTUnwrap(row.fileURL)), bytes)
        }
        c.stream.close()
    }

    func testBidirectionalCancellationPreservesUnsentAndRetryUsesFreshID() async throws {
        let s = try await server()
        try await s.enqueueText("first", mode: .aes256GCM)
        try await s.enqueueText("next", mode: .plaintext)
        let c = try await TestClient(server: s, deferServerClosure: true)
        let (first, _) = try await c.next()
        XCTAssertEqual(first.mode, .aes256GCM)
        let peerID = Data(repeating: 1, count: 16)
        try await c.open(peerID, mode: .hmacSHA256)
        try await c.send(.meta, .hmacSHA256, peerID, file(1).encode())
        // Keep incoming open while outgoing advances to WAIT_COMPLETE.
        try await c.send(.ack, first.mode, first.itemID, WireBody.ack(.ready))
        let (meta, _) = try await c.next()
        let (data, body) = try await c.next()
        let (end, _) = try await c.next()
        XCTAssertEqual([meta.type, data.type, end.type], [.meta, .data, .end])
        XCTAssertEqual(body, Data("first".utf8))
        let oldSession = await s.session
        await s.cancelActive()
        let result = await s.snapshot()
        let firstRow = try XCTUnwrap(result.records.first)
        XCTAssertTrue(result.paused)
        XCTAssertEqual(firstRow.state, .unconfirmed)
        XCTAssertEqual(result.records.map(\.state), [.unconfirmed, .queued, .interrupted])
        XCTAssertNil(result.records.last?.fileURL)
        try await s.retry(firstRow.id)
        let next = try await TestClient(server: s)
        let (queued, _) = try await next.next()
        XCTAssertEqual(queued.mode, .plaintext)
        try await next.send(.ack, queued.mode, queued.itemID, WireBody.ack(.ready))
        _ = try await next.next()
        _ = try await next.next()
        _ = try await next.next()
        try await next.send(.ack, queued.mode, queued.itemID, WireBody.ack(.complete))
        let (retry, _) = try await next.next()
        XCTAssertEqual(retry.mode, first.mode)
        XCTAssertNotEqual(retry.itemID, first.itemID)
        let reads = await c.stream.output.reads
        try await c.send(.ack, first.mode, first.itemID, WireBody.ack(.complete))
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        var delivered = await c.stream.output.reads
        while delivered == reads, ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(2))
            delivered = await c.stream.output.reads
        }
        XCTAssertGreaterThan(delivered, reads)
        c.stream.close()
        if let oldSession { await s.fail(SessionFault.closed, session: oldSession) }
        let fresh = await s.snapshot()
        XCTAssertTrue(fresh.connected)
        XCTAssertEqual(fresh.records.first?.state, .sending)
        await s.cancelActive()
    }

    func testDrainAcceptsEarlierOpenAndHoldsNewLocalJobUntilReconnect() async throws {
        let s = try await server()
        let c = try await TestClient(server: s)
        try await wait(s) { $0.connected }
        await s.exhaustTestBudget()
        try await s.enqueueText("pending", mode: .chacha20Poly1305)
        let (request, reqBody) = try await c.next()
        XCTAssertEqual(request.type, .drain)
        XCTAssertEqual(try WireBody.decodeDrain(reqBody), .request)
        let id = Data(repeating: 3, count: 16)
        try await c.open(id, mode: .aes256GCM)
        try await c.send(.drain, .plaintext, TestClient.zero, WireBody.drain(.request))
        try await c.send(.meta, .aes256GCM, id, file(0).encode())
        try await c.end(id, mode: .aes256GCM, bytes: Data(), chunks: 0)
        let (ack, ackBody) = try await c.next()
        XCTAssertEqual(ack.type, .ack)
        XCTAssertEqual(try WireBody.decodeAck(ackBody), .complete)
        let (ready, readyBody) = try await c.next()
        XCTAssertEqual(ready.type, .drain)
        XCTAssertEqual(try WireBody.decodeDrain(readyBody), .ready)
        try await c.send(.drain, .plaintext, TestClient.zero, WireBody.drain(.ready))
        c.stream.close()
        try await wait(s) { !$0.connected && !$0.paused }
        let second = try await TestClient(server: s)
        let (open, _) = try await second.next()
        XCTAssertEqual(open.type, .open)
        XCTAssertEqual(open.mode, .chacha20Poly1305)
        XCTAssertEqual(open.sequence, 0)
        await s.cancelActive()
    }

    func testRejectsPostRequestOpen() async throws {
        let s = try await server()
        let c = try await TestClient(server: s)
        try await c.send(.drain, .plaintext, TestClient.zero, WireBody.drain(.request))
        try await c.send(.open, .aes256GCM, Data(repeating: 5, count: 16), WireBody.open(.aes256GCM))
        let result = try await wait(s) { $0.status == "protocol_error" }
        XCTAssertTrue(result.records.isEmpty)
    }

    func testPeerIDs4096And4097() async throws {
        let s = try await server()
        let c = try await TestClient(server: s)
        try await wait(s) { $0.connected }
        await s.seedPeerIDsAtBoundary()
        let id = Data(repeating: 9, count: 16)
        try await c.open(id, mode: .hmacSHA256)
        try await c.send(.meta, .hmacSHA256, id, file(0).encode())
        try await c.end(id, mode: .hmacSHA256, bytes: Data(), chunks: 0)
        let (_, ack) = try await c.next()
        XCTAssertEqual(try WireBody.decodeAck(ack), .complete)
        try await c.send(.open, .hmacSHA256, Data(repeating: 10, count: 16), WireBody.open(.hmacSHA256))
        let result = try await wait(s) { $0.status == "protocol_error" }
        XCTAssertEqual(result.records.map(\.state), [.completed])
    }

    func testFailedCompleteACKWriteKeepsCommittedReceivedFile() async throws {
        let s = try await server()
        let c = try await TestClient(server: s)
        let id = Data(repeating: 7, count: 16)
        try await c.open(id, mode: .aes256GCM)
        try await c.send(.meta, .aes256GCM, id, file(0).encode())
        // Refuse the server's next write while still delivering the valid END.
        await c.stream.input.finish()
        try await c.end(id, mode: .aes256GCM, bytes: Data(), chunks: 0)
        let result = try await wait(s) { !$0.connected && $0.records.first?.state == .completed }
        let url = try XCTUnwrap(result.records.first?.fileURL)
        XCTAssertEqual(try Data(contentsOf: url), Data())
    }

    func testMetadataPersistenceFailureClosesWithoutCompleteACK() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let s = try await server(in: root)
        let c = try await TestClient(server: s)
        let id = Data(repeating: 15, count: 16)
        try await c.open(id, mode: .chacha20Poly1305)
        try await c.send(.meta, .chacha20Poly1305, id, file(0).encode())
        let active = try await wait(s) { $0.records.first?.metadata != nil }
        let uuid = try XCTUnwrap(active.records.first?.id)
        let staging = root.appendingPathComponent("receiving").appendingPathComponent(uuid.uuidString)
        try FileManager.default.createDirectory(at: staging.appendingPathComponent("record.json"),
                                                withIntermediateDirectories: false)
        try await c.end(id, mode: .chacha20Poly1305, bytes: Data(), chunks: 0)
        let failed = try await wait(s) { $0.status == "storage_error" }
        XCTAssertEqual(failed.records.first?.state, .interrupted)
        XCTAssertNil(failed.records.first?.fileURL)
        XCTAssertFalse(FileManager.default.fileExists(atPath: staging.path))
        do { _ = try await c.next(); XCTFail("Complete ACK was sent before durable commit") }
        catch SessionFault.closed { }
    }

    func testDuplicateEndIsRejectedAndCommittedItemSurvives() async throws {
        let s = try await server()
        let c = try await TestClient(server: s)
        let id = Data(repeating: 8, count: 16)
        try await c.open(id, mode: .plaintext)
        try await c.send(.meta, .plaintext, id, file(0).encode())
        try await c.end(id, mode: .plaintext, bytes: Data(), chunks: 0)
        _ = try await c.next()
        try await c.end(id, mode: .plaintext, bytes: Data(), chunks: 0)
        let result = try await wait(s) { $0.status == "protocol_error" }
        XCTAssertEqual(result.records.map(\.state), [.completed])
    }

    func testOppositeDirectionProgressDoesNotExtendReadyOrCompleteACKDeadline() async throws {
        let s = try await server()
        try await s.enqueueText("", mode: .aes256GCM)
        let c = try await TestClient(server: s)
        let (out, _) = try await c.next()
        let id = Data(repeating: 14, count: 16)
        let readyDeadline = try await s.presetACKDeadline()
        try await c.open(id, mode: .hmacSHA256)
        let afterOpen = await s.deadlines()
        XCTAssertEqual(readyDeadline, afterOpen.1)
        try await c.send(.ack, out.mode, out.itemID, WireBody.ack(.ready))
        _ = try await c.next() // META
        _ = try await c.next() // END; no DATA for empty text
        let completeDeadline = try await s.presetACKDeadline()
        try await c.send(.meta, .hmacSHA256, id, file(0).encode())
        try await c.end(id, mode: .hmacSHA256, bytes: Data(), chunks: 0)
        _ = try await c.next()
        let afterEnd = await s.deadlines()
        XCTAssertEqual(completeDeadline, afterEnd.1)
        try await c.send(.ack, out.mode, out.itemID, WireBody.ack(.complete))
        try await wait(s) { $0.records.allSatisfy { $0.state == .completed } }
        await s.cancelActive()
    }

    func testFirstByteStartsExactReadDeadline() async throws {
        let s = try await server()
        let c = try await TestClient(server: s)
        try await wait(s) { $0.connected }
        let idle = await s.deadlines()
        XCTAssertNil(idle.0)
        try await c.stream.write(Data([0]))
        let until = ContinuousClock.now.advanced(by: .seconds(3))
        var frameDeadline = await s.deadlines().0
        while frameDeadline == nil, ContinuousClock.now < until {
            await Task.yield()
            frameDeadline = await s.deadlines().0
        }
        await s.expirePartialRead()
        try await wait(s) { $0.status == "timeout" }
    }

    func testMalformedTransfersNeverPublish() async throws {
        enum Scenario: CaseIterable { case wrongID, wrongIndex, wrongCount, wrongHash, shortChunk, duplicateMeta, missingEnd }
        for error in Scenario.allCases {
            let s = try await server()
            let c = try await TestClient(server: s)
            let mode = ProtectionMode.plaintext
            let id = Data(repeating: 11, count: 16)
            try await c.open(id, mode: mode)
            let meta = try file(error == .shortChunk ? 65537 : 1).encode()
            try await c.send(.meta, mode, id, meta)
            let bytes = Data([0x4a])
            switch error {
            case .wrongID: try await c.send(.data, mode, Data(repeating: 12, count: 16), bytes)
            case .wrongIndex: try await c.send(.data, mode, id, bytes, index: 1)
            case .duplicateMeta: try await c.send(.meta, mode, id, meta)
            case .shortChunk: try await c.send(.data, mode, id, bytes)
            case .wrongCount, .wrongHash, .missingEnd:
                try await c.send(.data, mode, id, bytes)
                if error == .missingEnd { c.stream.close() }
                else {
                    try await c.end(id, mode: mode, bytes: error == .wrongHash ? Data([0x4b]) : bytes,
                                    chunks: error == .wrongCount ? 2 : 1)
                }
            }
            let snapshot = try await wait(s) { $0.records.first?.state == .interrupted }
            XCTAssertNil(snapshot.records.first?.fileURL)
            XCTAssertFalse(snapshot.connected)
        }
    }
}
