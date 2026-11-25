import XCTest
import Foundation
import CryptoKit
@testable import USBLinkCore

// A test-only ordered duplex stream. Short reads split headers, tags, and content.
// A bounded wait ensures a stalled state machine reports a test failure.
private actor BytePipe {
    var bytes = Data()
    var closed = false
    var reset = false
    var reads = 0

    func put(_ data: Data) throws {
        guard !closed else { throw SessionFault.closed }
        bytes.append(data)
    }

    func read(_ count: Int) async throws -> Data {
        let deadline = ContinuousClock.now.advanced(by: .seconds(6))
        while bytes.isEmpty {
            if reset { throw SessionFault.closed }
            if closed { return Data() }
            guard ContinuousClock.now < deadline else { throw SessionFault.timeout }
            try await Task.sleep(for: .milliseconds(1))
        }
        let part = Data(bytes.prefix(min(count, 3071)))
        bytes.removeFirst(part.count)
        reads += 1
        return part
    }

    func finish() { closed = true }
    func abort() { closed = true; reset = true }
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

    init(server: USBServer, psk: Data = TestClient.psk, deferServerClosure: Bool = false) async throws {
        let toServer = BytePipe(), toClient = BytePipe()
        stream = MemoryStream(toClient, toServer)
        await server.accept(MemoryStream(toServer, toClient, closeOnRequest: !deferServerClosure))
        let ch = try Wire.makeClientHello(psk: psk, nonce: randomBytes(32))
        try await stream.write(ch)
        let sh = try await stream.exact(73)
        let cf = try Wire.makeClientFinish(clientHello: ch, serverHello: sh, psk: psk)
        keys = try Wire.sessionKeys(clientHello: ch, serverHello: sh, clientFinish: cf, psk: psk)
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
    func readerTask() -> Task<Void, Never>? { session?.reader }
    func handshakeDeadline() -> ContinuousClock.Instant? { session?.handshakeDeadline }
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
    private func connectStream(_ server: USBServer, deferServerClosure: Bool = false) async -> MemoryStream {
        let toServer = BytePipe(), toClient = BytePipe()
        await server.accept(MemoryStream(toServer, toClient, closeOnRequest: !deferServerClosure))
        return MemoryStream(toClient, toServer)
    }

    func testFreshQRCommitsOnlyAfterFinishAndRelaunchUsesSavedKey() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let credential = root.appendingPathComponent("test-credential")
        let server = try USBServer(root: root, secret: nil, storeSecret: { key in
            try XCTUnwrap(key).write(to: credential, options: .atomic)
        }, invitationLifetime: 1)
        await server.setForeground(true)
        let candidate = randomBytes(32)
        try await server.preparePairing(.qr(candidate))
        let waiting = await server.snapshot()
        XCTAssertEqual(waiting.pairingStatus, .ready)
        XCTAssertFalse(waiting.paired)
        let stream = await connectStream(server)
        let hello = try Wire.makeClientHello(psk: candidate, nonce: randomBytes(32))
        try await stream.write(Data(hello.prefix(3)))
        try await stream.write(Data(hello.dropFirst(3)))
        let reply = try await stream.exact(Wire.handshakeHelloSize)
        let confirming = await server.snapshot()
        XCTAssertEqual(confirming.pairingStatus, .connecting)
        XCTAssertNil(confirming.pairingExpiresAt)
        XCTAssertFalse(confirming.connected)
        XCTAssertFalse(FileManager.default.fileExists(atPath: credential.path))
        // After the correct prefix the ULAB handshake gets its own ten-second budget.
        try await Task.sleep(for: .milliseconds(1100))
        let finish = try Wire.makeClientFinish(clientHello: hello, serverHello: reply, psk: candidate)
        try await stream.write(finish)
        try await wait(server) { $0.connected && $0.pairingStatus == .paired }
        XCTAssertTrue(try Data(contentsOf: credential) == candidate)
        await server.setForeground(false)

        let reopened = try USBServer(root: root, secret: Data(contentsOf: credential), storeSecret: { _ in
            XCTFail("Saved-key reconnect rewrote the credential")
        })
        await reopened.setForeground(true)
        let client = try await TestClient(server: reopened, psk: candidate)
        try await wait(reopened) { $0.connected }
        client.stream.close()
    }

    func testUnknownTruncatedAndWrongMethodPrefixesRetainTheArmedInvitation() async throws {
        let s = try await server()
        try await s.preparePairing(.sixDigitCode("004271"))
        let initial = await s.snapshot()
        XCTAssertEqual(try XCTUnwrap(initial.pairingExpiresAt).timeIntervalSinceNow, 120, accuracy: 2)
        let legacyHello = try Wire.makeClientHello(psk: TestClient.psk, nonce: randomBytes(32))
        for bytes in [Data("UNKNOWN!".utf8), Data(EnrollmentWire.magic.prefix(7)), legacyHello] {
            let stream = await connectStream(s)
            try await stream.write(bytes)
            await stream.output.finish()
            let eof = try await stream.read(upTo: 1)
            XCTAssertTrue(eof.isEmpty)
            let after = await s.snapshot()
            XCTAssertEqual(after.pairingStatus, .ready)
            XCTAssertEqual(after.pairingExpiresAt, initial.pairingExpiresAt)
        }
        let attempt = await connectStream(s)
        try await attempt.write(EnrollmentWire.magic)
        try await wait(s) { $0.pairingStatus == .connecting }
        // Reject an oversize stage-one header before consuming any declared body.
        try await attempt.write(Data([1, 2, 1]))
        try await wait(s) { $0.pairingStatus == .failed }
        let replay = await connectStream(s)
        try await replay.write(EnrollmentWire.magic)
        let eof = try await replay.read(upTo: 1)
        XCTAssertTrue(eof.isEmpty)
        let afterReplay = await s.snapshot()
        XCTAssertEqual(afterReplay.pairingStatus, .failed)
    }

    func testPrefixAndPAKEHaveDistinctDeadlinesAndPAKEIsCappedByInvitationLifetime() async throws {
        let s = try await server()
        try await s.preparePairing(.sixDigitCode("004271"))
        let before = ContinuousClock.now
        let long = await connectStream(s)
        let prefix = await s.handshakeDeadline()
        XCTAssertGreaterThanOrEqual(try XCTUnwrap(prefix), before.advanced(by: .seconds(5)))
        XCTAssertLessThanOrEqual(try XCTUnwrap(prefix), ContinuousClock.now.advanced(by: .seconds(5)))
        try await long.write(EnrollmentWire.magic)
        try await wait(s) { $0.pairingStatus == .connecting }
        let pake = await s.handshakeDeadline()
        XCTAssertGreaterThanOrEqual(try XCTUnwrap(pake), before.advanced(by: .seconds(20)))
        XCTAssertLessThanOrEqual(try XCTUnwrap(pake), ContinuousClock.now.advanced(by: .seconds(20)))
        await s.cancelPairing()

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let limited = try USBServer(root: root, secret: nil, invitationLifetime: 1)
        await limited.setForeground(true)
        try await limited.preparePairing(.sixDigitCode("004271"))
        let short = await connectStream(limited)
        let prefixCutoff = await limited.handshakeDeadline()
        try await short.write(EnrollmentWire.magic)
        try await wait(limited) { $0.pairingStatus == .connecting }
        let pakeCutoff = await limited.handshakeDeadline()
        XCTAssertEqual(pakeCutoff, prefixCutoff)
        try await wait(limited) { $0.pairingStatus == .failed && !$0.paired }
        let eof = try await short.read(upTo: 1)
        XCTAssertTrue(eof.isEmpty)
    }

    func testFailedQRAuthenticationOrStoragePreservesPreviousKeyAndRejectsCoalescedOpen() async throws {
        enum Failure { case wrongKey, badFinish, failedStore }
        for failure in [Failure.wrongKey, .badFinish, .failedStore] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: root) }
            let credential = root.appendingPathComponent("test-credential")
            let old = randomBytes(32), candidate = randomBytes(32)
            let server = try USBServer(root: root, secret: old, storeSecret: { key in
                guard failure != .failedStore else { throw SessionFault.storage }
                try XCTUnwrap(key).write(to: credential, options: .atomic)
            })
            try old.write(to: credential)
            await server.setForeground(true)
            try await server.preparePairing(.qr(candidate))
            let stream = await connectStream(server)
            let hello = try Wire.makeClientHello(psk: failure == .wrongKey ? old : candidate, nonce: randomBytes(32))
            try await stream.write(hello)
            if failure != .wrongKey {
                let reply = try await stream.exact(Wire.handshakeHelloSize)
                var finish = try Wire.makeClientFinish(clientHello: hello, serverHello: reply, psk: candidate)
                let keys = try Wire.sessionKeys(clientHello: hello, serverHello: reply, clientFinish: finish, psk: candidate)
                let open = WireBody.open(.plaintext)
                let header = FrameHeader(type: .open, mode: .plaintext, sequence: 0, itemID: randomBytes(16),
                                         index: 0, length: open.count)
                if failure == .badFinish { finish[finish.startIndex] ^= 1 }
                try await stream.write(finish + (try Wire.encode(header, body: open, keys: keys.c2s)))
            }
            let failed = try await wait(server) { $0.pairingStatus == .failed }
            XCTAssertTrue(failed.paired)
            XCTAssertFalse(failed.connected)
            XCTAssertTrue(failed.records.isEmpty)
            XCTAssertTrue(try Data(contentsOf: credential) == old)
            let eof = try await stream.read(upTo: 1)
            XCTAssertTrue(eof.isEmpty)
            let restored = try await TestClient(server: server, psk: old)
            try await wait(server) { $0.connected }
            restored.stream.close()
        }
    }

    func testExpiryAndBackgroundClosePrefixReaderWithoutConsumingAnotherInvitation() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let s = try USBServer(root: root, secret: nil, invitationLifetime: 0.35)
        await s.setForeground(true)
        let key = randomBytes(32)
        try await s.preparePairing(.qr(key))
        let hanging = await connectStream(s, deferServerClosure: true)
        let expiredReader = await s.readerTask()
        try await hanging.write(Data(Wire.magic.prefix(1)))
        try await wait(s) { $0.pairingStatus == .expired }
        try await s.preparePairing(.qr(key))
        try await hanging.write(Data(Wire.magic.dropFirst(1)))
        await hanging.output.finish()
        await expiredReader?.value
        let after = await s.snapshot()
        XCTAssertEqual(after.pairingStatus, .ready)
        let prefix = await connectStream(s)
        try await prefix.write(Data(Wire.magic.prefix(6)))
        await s.setForeground(false)
        let eof = try await prefix.read(upTo: 1)
        XCTAssertTrue(eof.isEmpty)
        await s.setForeground(true)
        let finished = await s.snapshot()
        XCTAssertEqual(finished.pairingStatus, .idle)
        XCTAssertFalse(finished.paired)
        XCTAssertFalse(finished.paused)
    }

    func testReplacedOrCancelledConsumedSessionCannotCommitLateFinish() async throws {
        let s = try await server()
        for cancel in [false, true] {
            let key = randomBytes(32)
            try await s.preparePairing(.qr(key))
            let late = await connectStream(s, deferServerClosure: true)
            let hello = try Wire.makeClientHello(psk: key, nonce: randomBytes(32))
            try await late.write(hello)
            let reply = try await late.exact(Wire.handshakeHelloSize)
            let finish = try Wire.makeClientFinish(clientHello: hello, serverHello: reply, psk: key)
            let previousReader = await s.readerTask()
            if cancel { await s.cancelPairing() }
            else { try await s.preparePairing(.qr(randomBytes(32))) }
            try await late.write(finish)
            await late.output.finish()
            await previousReader?.value
            let state = await s.snapshot()
            XCTAssertEqual(state.pairingStatus, cancel ? .idle : .ready)
            XCTAssertFalse(state.connected)
            let current = await s.secret
            XCTAssertTrue(current == TestClient.psk)
        }
    }

    func testEnrollmentWhilePausedKeepsTransfersPausedUntilResume() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let key = randomBytes(32)
        let s = try USBServer(root: root, secret: key)
        await s.setForeground(true)
        try await s.enqueueText("queued", mode: .aes256GCM)
        await s.cancelActive()
        try await s.preparePairing(.qr(key))
        let c = try await TestClient(server: s, psk: key)
        let authenticated = try await wait(s) { $0.connected && $0.pairingStatus == .paired }
        XCTAssertTrue(authenticated.paused)
        XCTAssertEqual(authenticated.records.map(\.state), [.queued])
        try await c.send(.open, .plaintext, randomBytes(16), WireBody.open(.plaintext))
        let paused = try await wait(s) { !$0.connected }
        XCTAssertTrue(paused.paused)
        XCTAssertEqual(paused.records.map(\.state), [.queued])
        await s.resume()
        let resumed = try await TestClient(server: s, psk: key)
        let (frame, _) = try await resumed.next()
        XCTAssertEqual(frame.type, .open)
        await s.cancelActive()
    }

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
        let oldReader = oldSession?.reader
        await s.cancelActive()
        XCTAssertTrue(oldReader?.isCancelled == true)
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
        // The old reader is deliberately cancelled at disconnect. Inject a
        // late failure callback without requiring it to resume a closed read.
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

    func testFirstReconnectWaitsForCleanDrainEOFAndFreshAuthentication() async throws {
        let s = try await server()
        let old = try await TestClient(server: s)
        try await old.send(.drain, .plaintext, TestClient.zero, WireBody.drain(.request))
        let (_, request) = try await old.next()
        XCTAssertEqual(try WireBody.decodeDrain(request), .request)
        let (_, ready) = try await old.next()
        XCTAssertEqual(try WireBody.decodeDrain(ready), .ready)
        try await s.enqueueText("fresh session", mode: .chacha20Poly1305)
        let originalGeneration = await s.session

        let clientInput = BytePipe(), serverInput = BytePipe()
        let next = MemoryStream(clientInput, serverInput)
        await s.accept(MemoryStream(serverInput, clientInput))
        let ch = try Wire.makeClientHello(psk: TestClient.psk, nonce: randomBytes(32))
        try await next.write(ch)
        let excessInput = BytePipe()
        await s.accept(MemoryStream(BytePipe(), excessInput))
        let rejected = try await excessInput.read(1)
        XCTAssertTrue(rejected.isEmpty)
        let waitingGeneration = await s.session
        XCTAssertTrue(waitingGeneration === originalGeneration)
        let unconsumed = await serverInput.bytes
        XCTAssertEqual(unconsumed, ch)
        let previous = await s.snapshot()
        XCTAssertEqual(previous.records.first?.state, .queued)

        try await old.send(.drain, .plaintext, TestClient.zero, WireBody.drain(.ready))
        await old.stream.output.finish() // Until now, EOF was deliberately withheld.
        let sh = try await next.exact(73)
        let cf = try Wire.makeClientFinish(clientHello: ch, serverHello: sh, psk: TestClient.psk)
        let keys = try Wire.sessionKeys(clientHello: ch, serverHello: sh, clientFinish: cf, psk: TestClient.psk)
        let handshaking = await s.snapshot()
        XCTAssertFalse(handshaking.connected)
        XCTAssertEqual(handshaking.records.first?.state, .queued)
        try await next.write(cf)
        let fixed = try await next.exact(76)
        let open = try Wire.decodeHeader(fixed, keys: keys.s2c, expectedSequence: 0)
        XCTAssertEqual(open.type, .open)
        XCTAssertEqual(open.mode, .chacha20Poly1305)
        await s.cancelActive()
    }


    func testIdleTransportLossAllowsSendingAfterFreshAuthentication() async throws {
        let s = try await server()
        let initial = try await TestClient(server: s)
        try await wait(s) { $0.connected }
        await initial.stream.output.abort()
        let disconnected = try await wait(s) { $0.status == "connection_closed" }
        XCTAssertFalse(disconnected.paused)

        let recovered = try await TestClient(server: s)
        try await wait(s) { $0.connected }
        try await s.enqueueText("new after reconnect", mode: .hmacSHA256)
        let (open, _) = try await recovered.next()
        XCTAssertEqual(open.type, .open)
        XCTAssertEqual(open.mode, .hmacSHA256)
        await s.cancelActive()
    }

    func testTransportLossAllowsFreshAuthenticationWithoutReplayingTheQueue() async throws {
        let s = try await server()
        let before = try await TestClient(server: s)
        try await wait(s) { $0.connected }
        try await s.enqueueText("interrupted", mode: .aes256GCM)
        let (first, _) = try await before.next()
        XCTAssertEqual(first.type, .open)
        try await s.enqueueText("waiting", mode: .hmacSHA256)
        await before.stream.output.abort()

        let disconnected = try await wait(s) { $0.status == "connection_closed" }
        XCTAssertTrue(disconnected.paused)
        XCTAssertEqual(disconnected.records.map(\.state), [.interrupted, .queued])

        // A failed attempt cannot prevent the saved peer trying again.
        let invalid = await connectStream(s)
        try await invalid.write(Data("invalid!".utf8))
        let eof = try await invalid.read(upTo: 1)
        XCTAssertTrue(eof.isEmpty)

        let after = try await TestClient(server: s)
        try await wait(s) { $0.connected }
        let incomingID = randomBytes(16)
        let content = Data("new message".utf8)
        let metadata = try ItemMetadata(kind: .text, name: "", mediaType: "text/plain; charset=utf-8",
                                        size: UInt64(content.count))
        try await after.open(incomingID, mode: .chacha20Poly1305)
        try await after.send(.meta, .chacha20Poly1305, incomingID, metadata.encode())
        try await after.send(.data, .chacha20Poly1305, incomingID, content)
        try await after.end(incomingID, mode: .chacha20Poly1305, bytes: content, chunks: 1)
        let (_, complete) = try await after.next()
        XCTAssertEqual(try WireBody.decodeAck(complete), .complete)
        let recovered = await s.snapshot()
        XCTAssertTrue(recovered.paused)
        XCTAssertEqual(recovered.records.map(\.state), [.interrupted, .queued, .completed])

        await s.resume()
        let (resumed, _) = try await after.next()
        XCTAssertEqual(resumed.type, .open)
        XCTAssertEqual(resumed.mode, .hmacSHA256)
        await s.cancelActive()
    }

    func testForegroundRecoverySurvivesRelaunchButExplicitCancellationRequiresResume() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let s = try await server(in: root)
        let initial = try await TestClient(server: s)
        try await wait(s) { $0.connected }
        try await s.enqueueText("active", mode: .plaintext)
        _ = try await initial.next()
        try await s.enqueueText("queued", mode: .aes256GCM)
        await s.setForeground(false)
        let eof = try await initial.stream.read(upTo: 1)
        XCTAssertTrue(eof.isEmpty)

        let reopened = try USBServer(root: root, secret: TestClient.psk)
        await reopened.setForeground(true)
        _ = try await TestClient(server: reopened)
        try await wait(reopened) { $0.connected && $0.paused }

        await reopened.cancelActive()
        await reopened.setForeground(false)
        let cancelled = try USBServer(root: root, secret: TestClient.psk)
        await cancelled.setForeground(true)
        let refused = await connectStream(cancelled)
        let blocked = try await refused.read(upTo: 1)
        XCTAssertTrue(blocked.isEmpty)

        await cancelled.resume()
        _ = try await TestClient(server: cancelled)
        try await wait(cancelled) { $0.connected && !$0.paused }
        await cancelled.cancelActive()
    }

    func testTransportResetAfterDrainReadyDiscardsWaitingReconnect() async throws {
        let s = try await server()
        let old = try await TestClient(server: s)
        try await old.send(.drain, .plaintext, TestClient.zero, WireBody.drain(.request))
        _ = try await old.next()
        let (_, ready) = try await old.next()
        XCTAssertEqual(try WireBody.decodeDrain(ready), .ready)
        let candidateInput = BytePipe()
        await s.accept(MemoryStream(BytePipe(), candidateInput))
        try await old.send(.drain, .plaintext, TestClient.zero, WireBody.drain(.ready))
        await old.stream.output.abort()
        let rejected = try await candidateInput.read(1)
        XCTAssertTrue(rejected.isEmpty)
        let failed = try await wait(s) { $0.status == "connection_closed" }
        XCTAssertTrue(failed.paused)
        XCTAssertFalse(failed.connected)
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
