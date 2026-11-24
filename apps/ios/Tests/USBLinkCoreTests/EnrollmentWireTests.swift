import CryptoKit
import Foundation
import ULABPairing
import XCTest
@testable import USBLinkCore

final class EnrollmentWireTests: XCTestCase {
    func testQRRequiresCanonicalBase64URLAndRejectsPublicFixture() throws {
        let fixture = try loadPairingVector()
        XCTAssertThrowsError(try PairingInvitation.scanQR(fixture.qr.ascii))

        let key = Data((0..<32).map { UInt8(255 - $0) })
        let payload = key.base64EncodedString()
        let canonical = payload.replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        let prefix = "ULABQR1:"
        guard case .qr(let decoded) = try PairingInvitation.scanQR(prefix + canonical) else {
            return XCTFail("Expected a QR invitation")
        }
        XCTAssertTrue(decoded == key)

        for invalid in [
            "ulabqr1:" + canonical, " " + prefix + canonical, prefix + canonical + "\n",
            prefix + canonical + "=", prefix + String(canonical.dropLast()),
            prefix + String(canonical.dropLast()) + "B", prefix + String(payload.dropLast())
        ] {
            XCTAssertThrowsError(try PairingInvitation.scanQR(invalid))
        }
    }

    func testCodePreservesSixLiteralASCIIDigits() throws {
        guard case .code(let code) = try PairingInvitation.sixDigitCode("041827") else {
            return XCTFail("Expected a code invitation")
        }
        XCTAssertTrue(code == "041827")
        for invalid in ["41827", "0041827", "041827\n", " 41827", "０４１８２７", "04182a"] {
            XCTAssertThrowsError(try PairingInvitation.sixDigitCode(invalid))
        }
    }

    func testSharedRecordFramingAndCryptographicVector() throws {
        let fixture = try loadPairingVector()
        var transcriptRecords = Data()
        for (offset, hex) in fixture.recordHexInOrder.enumerated() {
            let expected = try pairingBytes(hex)
            let stage = UInt8(offset + 1)
            let body = Data([0, stage, 255])
            let record = try EnrollmentWire.record(stage: stage, body: body)
            XCTAssertEqual(record, expected)
            transcriptRecords.append(record)
        }
        let transcript = Data(SHA256.hash(data: transcriptRecords))
        XCTAssertTrue(transcript == (try pairingBytes(fixture.transcriptSha256)))
        let secret = SymmetricKey(data: try pairingBytes(fixture.bridgeDerivedSecretHex))
        let keys = EnrollmentWire.Keys(sharedSecret: secret, transcript: transcript)
        for (label, key) in [("psk", keys.psk), ("client-confirm", keys.client), ("server-confirm", keys.server)] {
            let expected = try pairingBytes(XCTUnwrap(fixture.hkdfSha256[label]))
            XCTAssertTrue(key.withUnsafeBytes { Data($0) } == expected, "Invalid HKDF output for \(label)")
        }
        let clientTag = try pairingBytes(XCTUnwrap(fixture.confirmationHex["C"]))
        let serverTag = try pairingBytes(XCTUnwrap(fixture.confirmationHex["S"]))
        XCTAssertTrue(keys.acceptsClientTag(clientTag, transcript: transcript))
        XCTAssertFalse(keys.acceptsClientTag(serverTag, transcript: transcript))
        XCTAssertTrue(keys.serverTag(transcript: transcript) == serverTag)
    }

    func testFreshNativeExchangesPreserveLeadingZeroAndTrailingClientHello() async throws {
        let first = try await exchange(matchingCode: true, coalesceHello: true)
        let second = try await exchange(matchingCode: true)
        XCTAssertFalse(first.psk == second.psk)
    }

    func testFramingBoundsAreSpecificToTheRecordStage() throws {
        let maximumShare = Data(repeating: 0, count: 512)
        let record = try EnrollmentWire.record(stage: 1, body: maximumShare)
        XCTAssertEqual(record.prefix(11), clientMagic + Data([1, 2, 0]))
        for stage in [UInt8(5), 6] {
            for length in [31, 33] {
                XCTAssertThrowsError(try EnrollmentWire.record(stage: stage, body: Data(count: length)))
            }
        }
    }

    func testWrongCodeAndChangedConfirmationFail() async throws {
        await assertRejected { _ = try await self.exchange(matchingCode: false) }
        await assertRejected {
            _ = try await self.exchange(matchingCode: true, replaceClientTag: Data(repeating: 0, count: 32))
        }
    }

    func testCapturedConfirmationCannotBeReplayedWithFreshNativeShares() async throws {
        let completed = try await exchange(matchingCode: true)
        await assertRejected {
            _ = try await self.exchange(matchingCode: true, replaceClientTag: completed.clientTag)
        }
    }

    func testRejectsWrongMagicDirectionAndOversizedHeaderBeforeReadingBody() async throws {
        let body = Data([0x42])
        for (magic, header) in [
            (Data("UNKNOWN!".utf8), Data([1, 0, 1])),
            (clientMagic, Data([2, 0, 1])),
            (clientMagic, Data([1, 2, 1])),
            (clientMagic, Data([1, 0, 0]))
        ] {
            let (client, server) = pairingStreams()
            try await client.write(header + body)
            await assertRejected {
                let deadline = ContinuousClock.now.advanced(by: .seconds(2))
                _ = try await EnrollmentWire.respond(on: server, code: "041827", initialMagic: magic, deadline: deadline)
            }
            let expectedCount = magic == clientMagic ? body.count : header.count + body.count
            let pendingCount = await server.input.bytes.count
            XCTAssertEqual(pendingCount, expectedCount)
        }
    }

    func testRejectsMalformedNativeShare() async throws {
        let (client, server) = pairingStreams()
        try await client.write(clientRecord(1, Data([0xff])))
        await assertRejected { _ = try await respond(server) }
        let output = try await client.read(upTo: 1)
        XCTAssertTrue(output.isEmpty)
    }

    func testExpiredDeadlineClosesBeforeReadingRecord() async throws {
        let (client, server) = pairingStreams()
        try await client.write(Data([1, 0, 1, 0xff]))
        do {
            _ = try await EnrollmentWire.respond(on: server, code: "041827", initialMagic: clientMagic,
                                                 deadline: .now.advanced(by: .seconds(-1)))
            XCTFail("Expired responder accepted a record")
        } catch {
            XCTAssertEqual(error as? SessionFault, .timeout)
        }
        let pendingCount = await server.input.bytes.count
        XCTAssertEqual(pendingCount, 4)
    }

    func testCancellationClosesPendingEnrollmentRead() async throws {
        let (client, server) = pairingStreams()
        // Leave the responder waiting for the rest of stage 1 after dispatch.
        try await client.write(clientMagic + Data([1]))
        let pending = Task { try await respond(server) }
        defer { pending.cancel() }
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while await server.input.pendingReads == 0 {
            guard ContinuousClock.now < deadline else { return XCTFail("Responder never awaited stage 1") }
            try await Task.sleep(for: .milliseconds(1))
        }
        pending.cancel()
        let eof = try await client.read(upTo: 1)
        XCTAssertTrue(eof.isEmpty)
        do {
            _ = try await pending.value
            XCTFail("Cancelled responder returned a candidate")
        } catch is CancellationError {}
    }

    private func assertRejected(_ operation: () async throws -> Void, file: StaticString = #filePath,
                                line: UInt = #line) async {
        do {
            try await operation()
            XCTFail("Responder accepted an invalid enrollment", file: file, line: line)
        } catch {
            XCTAssertEqual(error as? SessionFault, .protocolViolation, file: file, line: line)
        }
    }

    private func exchange(matchingCode: Bool, coalesceHello: Bool = false,
                          replaceClientTag: Data? = nil) async throws -> (psk: Data, clientTag: Data) {
        let (client, server) = pairingStreams()
        async let response = respond(server)
        defer { client.close() }

        let code = Data((matchingCode ? "041827" : "041828").utf8)
        let context = try XCTUnwrap(code.withUnsafeBytes { (bytes: UnsafeRawBufferPointer) in
            ulab_pake_new(Int32(ULAB_PAKE_CLIENT), bytes.bindMemory(to: UInt8.self).baseAddress, bytes.count)
        })
        defer { ulab_pake_free(context) }

        var records = Data()
        for round in Int32(1)...2 {
            var output = [UInt8](repeating: 0, count: 512)
            var written = 0
            let result = output.withUnsafeMutableBufferPointer { buffer in
                ulab_pake_write(context, round, buffer.baseAddress, buffer.count, &written)
            }
            guard result == 0, (1...512).contains(written) else { throw TestClientError.native }
            let stage = UInt8(2 * round - 1)
            let sent = clientRecord(stage, Data(output.prefix(written)))
            try await client.write(sent)
            let reply = try await clientReceive(client, stage: stage + 1)
            let status = reply.body.withUnsafeBytes { (bytes: UnsafeRawBufferPointer) in
                ulab_pake_read(context, round, bytes.bindMemory(to: UInt8.self).baseAddress, bytes.count)
            }
            guard status == 0 else { throw TestClientError.native }
            records.append(sent)
            records.append(reply.record)
        }

        var secret = Data(count: 32)
        let derived = secret.withUnsafeMutableBytes { (bytes: UnsafeMutableRawBufferPointer) in
            ulab_pake_derive(context, bytes.bindMemory(to: UInt8.self).baseAddress)
        }
        defer { secret.resetBytes(in: 0..<secret.count) }
        guard derived == 0 else { throw TestClientError.native }
        let transcript = Data(SHA256.hash(data: records))
        let ikm = SymmetricKey(data: secret)
        func derive(_ label: String) -> SymmetricKey {
            HKDF<SHA256>.deriveKey(inputKeyMaterial: ikm, salt: transcript,
                                  info: Data("ulab-ecjpake/1/\(label)".utf8), outputByteCount: 32)
        }
        let psk = derive("psk").withUnsafeBytes { Data($0) }
        let tag = Data(HMAC<SHA256>.authenticationCode(for: Data("ULABP001/C\0".utf8) + transcript,
                                                      using: derive("client-confirm")))
        let hello = try (coalesceHello ? Wire.makeClientHello(psk: psk, nonce: randomBytes(32)) : Data())
        try await client.write(clientRecord(5, replaceClientTag ?? tag) + hello)
        if !matchingCode || replaceClientTag != nil {
            // Wait for the server's actual failure before checking that no stage 6 was emitted.
            do {
                _ = try await response
            } catch {
                let output = try await client.read(upTo: 1)
                XCTAssertTrue(output.isEmpty)
                throw error
            }
            throw TestClientError.expectedRejection
        }
        let confirmation = try await clientReceive(client, stage: 6)
        XCTAssertTrue(HMAC<SHA256>.isValidAuthenticationCode(confirmation.body,
                      authenticating: Data("ULABP001/S\0".utf8) + transcript, using: derive("server-confirm")))
        let responderPSK = try await response
        XCTAssertTrue(responderPSK == psk)
        if coalesceHello {
            let preservedHello = try await server.exact(hello.count)
            XCTAssertTrue(preservedHello == hello)
            _ = try Wire.makeServerHello(clientHello: preservedHello, psk: responderPSK, nonce: randomBytes(32))
        }
        return (psk, tag)
    }
}

private let clientMagic = Data("ULABP001".utf8)

private func respond(_ stream: any SessionStream) async throws -> Data {
    let prefix = try await stream.exact(8)
    return try await EnrollmentWire.respond(on: stream, code: "041827", initialMagic: prefix,
                                            deadline: .now.advanced(by: .seconds(5)))
}

private enum TestClientError: Error { case native, invalidRecord, expectedRejection }

private func clientRecord(_ stage: UInt8, _ body: Data) -> Data {
    let length = UInt16(body.count)
    return clientMagic + Data([stage, UInt8(length >> 8), UInt8(truncatingIfNeeded: length)]) + body
}

private func clientReceive(_ stream: any SessionStream, stage: UInt8) async throws -> (record: Data, body: Data) {
    let header = try await stream.exact(11)
    let length = Int(header[9]) * 256 + Int(header[10])
    guard header.prefix(8) == clientMagic, header[8] == stage,
          stage == 6 ? length == 32 : (1...512).contains(length) else {
        throw TestClientError.invalidRecord
    }
    let body = try await stream.exact(length)
    return (header + body, body)
}

private actor PairingBuffer {
    var bytes = Data()
    var pendingReads = 0
    private var closed = false

    func read(_ count: Int) async throws -> Data {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        pendingReads += 1
        defer { pendingReads -= 1 }
        while bytes.isEmpty && !closed {
            guard ContinuousClock.now < deadline else { throw SessionFault.timeout }
            // The stream simulates closing blocked I/O independently of task cancellation.
            await waitForInput()
        }
        let part = Data(bytes.prefix(min(count, 3)))
        bytes.removeFirst(part.count)
        return part
    }

    private func waitForInput() async {
        await withCheckedContinuation { continuation in
            DispatchQueue.global().asyncAfter(deadline: .now() + .milliseconds(1)) { continuation.resume() }
        }
    }

    func write(_ value: Data) throws {
        guard !closed else { throw SessionFault.closed }
        bytes.append(value)
    }

    func finish() { closed = true }
}

private final class PairingStream: SessionStream, Sendable {
    let input: PairingBuffer
    let output: PairingBuffer

    init(input: PairingBuffer, output: PairingBuffer) {
        self.input = input
        self.output = output
    }

    func read(upTo count: Int) async throws -> Data { try await input.read(count) }
    func write(_ bytes: Data) async throws { try await output.write(bytes) }
    func close() { Task { await input.finish(); await output.finish() } }
}

private func pairingStreams() -> (client: PairingStream, server: PairingStream) {
    let incoming = PairingBuffer()
    let outgoing = PairingBuffer()
    return (PairingStream(input: outgoing, output: incoming), PairingStream(input: incoming, output: outgoing))
}

private struct PairingVector: Decodable {
    struct QR: Decodable { let ascii: String }

    let qr: QR
    let recordHexInOrder: [String]
    let transcriptSha256: String
    let bridgeDerivedSecretHex: String
    let hkdfSha256: [String: String]
    let confirmationHex: [String: String]
}

private func loadPairingVector() throws -> PairingVector {
    var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    while true {
        let path = directory.appendingPathComponent("protocol/vectors/pairing-v1.json")
        if FileManager.default.fileExists(atPath: path.path) {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            return try decoder.decode(PairingVector.self, from: Data(contentsOf: path))
        }
        let parent = directory.deletingLastPathComponent()
        guard parent.path != directory.path else { throw CocoaError(.fileNoSuchFile) }
        directory = parent
    }
}

private func pairingBytes(_ hex: String) throws -> Data {
    let digits = Array(hex.utf8)
    guard digits.count.isMultiple(of: 2) else { throw CocoaError(.fileReadCorruptFile) }
    return try Data(stride(from: 0, to: digits.count, by: 2).map { index in
        try XCTUnwrap(UInt8(String(decoding: digits[index...index + 1], as: UTF8.self), radix: 16))
    })
}
