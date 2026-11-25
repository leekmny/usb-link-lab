import CryptoKit
import Foundation
import XCTest
@testable import USBLinkCore

final class WireTests: XCTestCase {
    private let psk = Data(0x00...0x1f)
    private let nc = Data(0x20...0x3f)
    private let ns = Data(0x40...0x5f)
    private let itemID = Data(0x00...0x0f)
    private let message = bytes("ec9588eb85952c20555342210a")

    func testAppendixHandshakeAndKeys() throws {
        let (ch, sh, cf) = try handshake()
        XCTAssertEqual(ch, Data("ULAB0001".utf8) + Data([1]) + nc + bytes(
            "8a5a63503ca163aa238da8e13d2d5662ec7fbd03df6c1eeb10317c83452efee0"
        ))
        XCTAssertEqual(sh, Data("ULAB0001".utf8) + Data([2]) + ns + bytes(
            "382c3a8f8c92fc2e5a9c2e9db914f59bc4dbf6e8102e091179fe3b821f978b75"
        ))
        XCTAssertEqual(cf, Data("ULAB0001".utf8) + Data([3]) + bytes(
            "7156cf54bef493f11331f2119980a5d0901c365f38d53bf24162c21c304fd357"
        ))
        XCTAssertEqual(Data(SHA256.hash(data: ch + sh + cf)), bytes(
            "a09adab89f2ad1fce26eb1f21e23e8a1094ddfeb9c4c05cdf83b45ed2752cb53"
        ))
        let keys = try Wire.sessionKeys(clientHello: ch, serverHello: sh, clientFinish: cf, psk: psk).c2s
        assertKey(keys.hdr, "f23507808fa3b0ce6e3106d9797939e3bbedca8196249eef2df2249f54ecfd8d")
        assertKey(keys.aes, "436418f1f2ee0db243a50b6c7312b5e68db32a59d0331bce7e014f525b7a1972")
        assertKey(keys.chacha, "95b7791c4eb4e6d88d15617cce92fac26bfc0bcdfd4fae8de0d2a11a530f3b57")
        assertKey(keys.hmac, "207fb0300784e7d80fcafb8685152102b80d448bbf4e4eeb328bd586b4156459")
    }

    func testHandshakeRejectsWrongSecretAndChangedRecords() throws {
        let (ch, sh, cf) = try handshake()
        XCTAssertThrowsError(try Wire.makeServerHello(clientHello: ch, psk: flipped(psk, at: 0), nonce: ns))
        XCTAssertThrowsError(try Wire.makeServerHello(clientHello: ch, psk: psk, nonce: Data(repeating: 0, count: 31)))
        for offset in [0, 8, 9, 41, 72] {
            XCTAssertThrowsError(try Wire.makeServerHello(clientHello: flipped(ch, at: offset), psk: psk, nonce: ns))
            XCTAssertThrowsError(try Wire.makeClientFinish(clientHello: ch, serverHello: flipped(sh, at: offset), psk: psk))
        }
        for offset in [0, 8, 9, 40] {
            XCTAssertThrowsError(try Wire.sessionKeys(
                clientHello: ch, serverHello: sh, clientFinish: flipped(cf, at: offset), psk: psk
            ))
        }
    }

    func testFreshHandshakeChangesKeysAndRejectsOldFinish() throws {
        let (ch, sh, cf) = try handshake()
        let secondSH = try Wire.makeServerHello(clientHello: ch, psk: psk, nonce: flipped(ns, at: 0))
        let secondCF = try Wire.makeClientFinish(clientHello: ch, serverHello: secondSH, psk: psk)
        let first = try Wire.sessionKeys(clientHello: ch, serverHello: sh, clientFinish: cf, psk: psk)
        let second = try Wire.sessionKeys(clientHello: ch, serverHello: secondSH, clientFinish: secondCF, psk: psk)
        XCTAssertNotEqual(first.c2s.hdr, second.c2s.hdr)
        XCTAssertNotEqual(first.c2s.hdr, first.s2c.hdr)
        XCTAssertThrowsError(try Wire.sessionKeys(clientHello: ch, serverHello: secondSH, clientFinish: cf, psk: psk))
    }

    func testAppendixDataFrames() throws {
        let keys = try fixtureKeys().c2s
        let cases: [(ProtectionMode, String, String?, String)] = [
            (.plaintext, "00000055", nil, "ec9588eb85952c20555342210a"),
            (.aes256GCM, "00000065", "69b21eeb63dee7bf2b1ea369dba6aab155a750941a5031afc0d4691ac550c4eb",
             "bb849151471526ecff5220478dd9a5931597bfac524fc3b554dfcb4ee1"),
            (.chacha20Poly1305, "00000065", "e4bc61e2c4081ee4b6008bd052d7662e0778e54ba81cb932dc7bf9a76ce7bb8e",
             "53695f5666e7845130fd75d8a289094e2f8a60ef5290d3e3bef04ece72"),
            (.hmacSHA256, "00000075", nil,
             "ec9588eb85952c20555342210af5eaa80d078481d50b54ee0b9a4a76cd2edf79daeb00b78508fe01bbb07bf342")
        ]
        for (mode, prefix, mac, body) in cases {
            let header = dataHeader(mode: mode)
            let record = try Wire.encode(header, body: message, keys: keys)
            let expectedHead = bytes(prefix) + bytes("0103") + Data([mode.rawValue]) + bytes(
                "000000000000000002000102030405060708090a0b0c0d0e0f00000000000000000000000d"
            )
            XCTAssertEqual(record.prefix(44), expectedHead, "\(mode)")
            if let mac { XCTAssertEqual(record.dropFirst(44).prefix(32), bytes(mac), "\(mode)") }
            XCTAssertEqual(record.dropFirst(76), bytes(body), "\(mode)")
            try assertDecoded(record, header, message, keys)
        }
    }

    func testRejectsUnauthenticatedHeaderChangesAndSequenceReplayOrGap() throws {
        let keys = try fixtureKeys()
        let record = try Wire.encode(dataHeader(mode: .aes256GCM), body: message, keys: keys.c2s)
        let fixed = Data(record.prefix(76))
        for offset in [3, 4, 6, 7, 15, 16, 43, 75] {
            XCTAssertThrowsError(try Wire.decodeHeader(flipped(fixed, at: offset), keys: keys.c2s, expectedSequence: 2))
        }
        for wrongSequence: UInt64 in [1, 3] {
            XCTAssertThrowsError(try Wire.decodeHeader(fixed, keys: keys.c2s, expectedSequence: wrongSequence))
        }
        XCTAssertThrowsError(try Wire.decodeHeader(fixed, keys: keys.s2c, expectedSequence: 2))
    }

    func testRejectsAuthenticatedHeadersWithInvalidValues() throws {
        let keys = try fixtureKeys().c2s
        let record = try Wire.encode(dataHeader(mode: .aes256GCM), body: message, keys: keys)
        let normal = Data(record.prefix(44))
        let fields: [(Int, UInt8)] = [(3, 0x64), (4, 2), (5, 0), (6, 4), (7, 1), (43, 0)]
        for (offset, value) in fields {
            var candidate = normal
            candidate[offset] = value
            let fixed = macHeader(candidate, keys.hdr)
            XCTAssertThrowsError(try Wire.decodeHeader(fixed, keys: keys, expectedSequence: 2), "offset \(offset)")
        }
        var zeroID = normal
        zeroID.replaceSubrange(16..<32, with: Data(repeating: 0, count: 16))
        XCTAssertThrowsError(try Wire.decodeHeader(macHeader(zeroID, keys.hdr), keys: keys, expectedSequence: 2))
        var overSequence = normal
        overSequence.replaceSubrange(8..<16, with: bytes("0000000100000000"))
        XCTAssertThrowsError(try Wire.decodeHeader(
            macHeader(overSequence, keys.hdr), keys: keys, expectedSequence: 4_294_967_296
        ))
    }

    func testPrefixAndBodyBounds() throws {
        XCTAssertEqual(try Wire.prefixLength(bytes("00000048")), 72)
        XCTAssertEqual(try Wire.prefixLength(bytes("00100000")), 1_048_576)
        for value in ["00000047", "00100001", "ffffffff", "000000", "0000004800"] {
            XCTAssertThrowsError(try Wire.prefixLength(bytes(value)), value)
        }
        let keys = try fixtureKeys().c2s
        for length in [0, 65_537] {
            let header = FrameHeader(type: .data, mode: .plaintext, sequence: 0, itemID: itemID, index: 0, length: length)
            XCTAssertThrowsError(try Wire.encode(header, body: Data(repeating: 0, count: length), keys: keys))
        }
        let json = Data(repeating: 0x20, count: 4_097)
        let header = FrameHeader(type: .open, mode: .plaintext, sequence: 0, itemID: itemID, index: 0, length: json.count)
        XCTAssertThrowsError(try Wire.encode(header, body: json, keys: keys))
    }

    func testControlFramesRequireControlAuthenticationEvenInPlaintext() throws {
        let keys = try fixtureKeys().c2s
        let body = Data(#"{"mode":0}"#.utf8)
        let header = FrameHeader(type: .open, mode: .plaintext, sequence: 0, itemID: itemID, index: 0, length: body.count)
        let record = try Wire.encode(header, body: body, keys: keys)
        XCTAssertEqual(record.dropFirst(76).count, body.count + 32)
        XCTAssertEqual(record.dropFirst(76).prefix(body.count), body)
        try assertDecoded(record, header, body, keys)
        XCTAssertThrowsError(try assertDecoded(flipped(record, at: 76), header, body, keys))
        let wrongIndex = FrameHeader(type: .open, mode: .plaintext, sequence: 0, itemID: itemID, index: 1, length: body.count)
        XCTAssertThrowsError(try Wire.encode(wrongIndex, body: body, keys: keys))
        let wrongID = FrameHeader(type: .error, mode: .plaintext, sequence: 0, itemID: itemID, index: 0, length: body.count)
        XCTAssertThrowsError(try Wire.encode(wrongID, body: body, keys: keys))
    }

    func testProtectedBodiesRejectContentTagAndTruncation() throws {
        let keys = try fixtureKeys().c2s
        for mode in [ProtectionMode.aes256GCM, .chacha20Poly1305, .hmacSHA256] {
            let header = dataHeader(mode: mode)
            let record = try Wire.encode(header, body: message, keys: keys)
            for offset in [76, record.count - 1] {
                XCTAssertThrowsError(try assertDecoded(flipped(record, at: offset), header, message, keys))
            }
            XCTAssertThrowsError(try assertDecoded(Data(record.dropLast()), header, message, keys))
        }
    }

    func testPlaintextBodyChangeDoesNotBecomeAuthenticatedThroughHeaderVerification() throws {
        let keys = try fixtureKeys().c2s
        let header = dataHeader(mode: .plaintext)
        let changed = flipped(try Wire.encode(header, body: message, keys: keys), at: 76)
        try assertDecoded(changed, header, flipped(message, at: 0), keys)
    }

    func testFlatSchemaRejectsDuplicateKeysAndNonIntegerTokens() throws {
        let invalid = [
            #"{"mode":0,"mode":0}"#, #"{"mode":0,"\u006dode":0}"#,
            #"{"mode":false}"#, #"{"mode":0.0}"#, #"{"mode":0e0}"#,
            #"{"mode":-0}"#, #"{"mode":+0}"#, #"{"mode":00}"#, #"{"mode":"0"}"#,
            #"{"mode":18446744073709551616}"#, #"{"mode":null}"#,
            #"{"mode":[0]}"#, #"{"mode":{"x":0}}"#, #"{"mode":0,}"#,
            #"{"mode":0,"extra":0}"#, #"{}"#, #"{"mode":1}"#, #"{"mode":0}{}"#
        ]
        for value in invalid {
            XCTAssertThrowsError(try WireBody.decodeOpen(Data(value.utf8), mode: .plaintext), value)
        }
        try WireBody.decodeOpen(Data(" \n{\r\"\\u006dode\"\t:\t0}\r ".utf8), mode: .plaintext)
        XCTAssertThrowsError(try WireBody.decodeOpen(bytes("efbbbf") + WireBody.open(.plaintext), mode: .plaintext))
    }

    func testMetadataRejectsInvalidUnicodeAndPreservesValidScalars() throws {
        let escapedEmoji = Data(#"{"kind":"file","name":"\uD83D\uDE00","media_type":"application/octet-stream","size":0,"chunk_size":65536}"#.utf8)
        XCTAssertEqual(try ItemMetadata.decode(escapedEmoji).name, "😀")
        for invalid in [#"{"stage":"\uD800"}"#, #"{"stage":"\uDC00"}"#, #"{"stage":"\uD83D\u0041"}"#] {
            XCTAssertThrowsError(try WireBody.decodeAck(Data(invalid.utf8)))
        }
        let opening = Data(#"{"kind":"file","name":""#.utf8)
        let closing = Data(#"","media_type":"application/octet-stream","size":0,"chunk_size":65536}"#.utf8)
        XCTAssertThrowsError(try ItemMetadata.decode(opening + bytes("eda080") + closing))
        XCTAssertThrowsError(try ItemMetadata.decode(opening + bytes("c080") + closing))
        for validName in ["\u{FEFF}", "\u{FFFD}", "사진.bin"] {
            XCTAssertEqual(try ItemMetadata.decode(opening + Data(validName.utf8) + closing).name, validName)
        }
    }

    func testMetadataEnforcesKindSizeMediaAndSafeFilename() throws {
        let maximum = try ItemMetadata(kind: .file, name: String(repeating: "a", count: 255), mediaType: "x", size: 1_073_741_824)
        XCTAssertEqual(try ItemMetadata.decode(maximum.encode()), maximum)
        XCTAssertThrowsError(try ItemMetadata(kind: .file, name: "file", mediaType: "x", size: 1_073_741_825))
        XCTAssertThrowsError(try ItemMetadata(kind: .text, name: "", mediaType: "text/plain; charset=utf-8", size: 1_048_577))
        XCTAssertThrowsError(try ItemMetadata(kind: .text, name: "name", mediaType: "text/plain; charset=utf-8", size: 0))
        XCTAssertThrowsError(try ItemMetadata(kind: .image, name: "image", mediaType: "application/octet-stream", size: 0))
        XCTAssertThrowsError(try ItemMetadata(kind: .file, name: "file", mediaType: "x\n", size: 0))
        XCTAssertThrowsError(try ItemMetadata(kind: .file, name: "file", mediaType: String(repeating: "a", count: 128), size: 0))
        XCTAssertThrowsError(try ItemMetadata(kind: .file, name: "file", mediaType: "x", size: 0, chunkSize: 1))
        for name in ["", ".", "..", "../file", "dir/file", #"dir\file"#, "a\u{0}b", "a\u{85}b",
                     "a:b", "a?b", "a<b", "a>b", "a|b", "a\"b", "a*b", "file.", "file ",
                     "CON.txt", "aux .png", "Lpt².txt", "com9", String(repeating: "가", count: 86)] {
            XCTAssertThrowsError(try ItemMetadata(kind: .file, name: name, mediaType: "x", size: 0), "\(name.debugDescription)")
        }
    }

    func testControlAndEndSchemas() throws {
        XCTAssertEqual(try WireBody.decodeAck(WireBody.ack(.ready)), .ready)
        XCTAssertEqual(try WireBody.decodeAck(WireBody.ack(.complete)), .complete)
        XCTAssertEqual(try WireBody.decodeDrain(WireBody.drain(.request)), .request)
        XCTAssertEqual(try WireBody.decodeDrain(WireBody.drain(.ready)), .ready)
        try WireBody.decodeError(WireBody.error)
        XCTAssertThrowsError(try WireBody.decodeAck(Data(#"{"stage":"request"}"#.utf8)))
        XCTAssertThrowsError(try WireBody.decodeDrain(Data(#"{"stage":"complete"}"#.utf8)))
        XCTAssertThrowsError(try WireBody.decodeError(Data(#"{"code":"other"}"#.utf8)))
        let emptyHash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        let end = try WireBody.decodeEnd(WireBody.end(bytes: 0, chunks: 0, sha256: emptyHash))
        XCTAssertEqual(end.bytes, 0)
        XCTAssertEqual(end.chunks, 0)
        XCTAssertEqual(end.sha256, emptyHash)
        XCTAssertThrowsError(try WireBody.end(bytes: 0, chunks: 0, sha256: emptyHash.uppercased()))
        let highest = try WireBody.decodeEnd(WireBody.end(bytes: 1_073_741_824, chunks: 16_384, sha256: emptyHash))
        XCTAssertEqual(highest.bytes, 1_073_741_824)
        XCTAssertEqual(highest.chunks, 16_384)
        for counts in [(1_073_741_825, 16_384), (1_073_741_824, 16_385)] {
            let body = Data("{\"bytes\":\(counts.0),\"chunks\":\(counts.1),\"sha256\":\"\(emptyHash)\"}".utf8)
            XCTAssertThrowsError(try WireBody.decodeEnd(body))
        }
    }

    func testSharedExchangeRecordsInBothDirections() throws {
        let corpus = try loadCorpus()
        let secret = bytes(corpus.handshake.psk)
        let ch = try Wire.makeClientHello(psk: secret, nonce: bytes(corpus.handshake.nc))
        let sh = try Wire.makeServerHello(clientHello: ch, psk: secret, nonce: bytes(corpus.handshake.ns))
        let cf = try Wire.makeClientFinish(clientHello: ch, serverHello: sh, psk: secret)
        XCTAssertEqual(ch, bytes(corpus.handshake.clientHello))
        XCTAssertEqual(sh, bytes(corpus.handshake.serverHello))
        XCTAssertEqual(cf, bytes(corpus.handshake.clientFinish))
        XCTAssertEqual(Data(SHA256.hash(data: ch + sh + cf)), bytes(corpus.handshake.transcriptHash))
        let keys = try Wire.sessionKeys(clientHello: ch, serverHello: sh, clientFinish: cf, psk: secret)

        for (direction, actual) in [("c2s", keys.c2s), ("s2c", keys.s2c)] {
            let expected = try XCTUnwrap(corpus.handshake.keys[direction])
            for (label, key) in [("hdr", actual.hdr), ("ctl", actual.ctl), ("aes", actual.aes),
                                 ("chacha", actual.chacha), ("hmac", actual.hmac)] {
                assertKey(key, try XCTUnwrap(expected[label]))
            }
        }

        XCTAssertEqual(Set(corpus.exchanges.map(\.origin)), ["c2s", "s2c"])
        XCTAssertEqual(Set(corpus.exchanges.map(\.mode)), Set(ProtectionMode.allCases.map(\.rawValue)))
        for exchange in corpus.exchanges {
            let mode = try XCTUnwrap(ProtectionMode(rawValue: exchange.mode))
            var sequences = ["c2s": UInt64(0), "s2c": UInt64(0)]
            var content = Data()
            var metadata: ItemMetadata?
            var chunks: UInt64 = 0

            for frame in exchange.records {
                let directionSequence = try XCTUnwrap(sequences[frame.direction])
                XCTAssertEqual(frame.sequence, directionSequence, exchange.label)
                sequences[frame.direction] = directionSequence + 1
                let key = frame.direction == "c2s" ? keys.c2s : keys.s2c
                let body = bytes(frame.body)
                let header = try FrameHeader(
                    type: XCTUnwrap(FrameType(rawValue: frame.type)), mode: mode, sequence: directionSequence,
                    itemID: bytes(frame.itemId), index: frame.index, length: body.count
                )
                let expected = bytes(frame.record)
                XCTAssertEqual(try Wire.encode(header, body: body, keys: key), expected, exchange.label)
                try assertDecoded(expected, header, body, key)

                switch header.type {
                case .open:
                    try WireBody.decodeOpen(body, mode: mode)
                case .meta:
                    metadata = try ItemMetadata.decode(body)
                case .data:
                    XCTAssertEqual(header.index, chunks)
                    chunks += 1
                    content.append(body)
                case .end:
                    let end = try WireBody.decodeEnd(body)
                    XCTAssertEqual(end.bytes, UInt64(content.count))
                    XCTAssertEqual(end.bytes, try XCTUnwrap(metadata).size)
                    XCTAssertEqual(end.chunks, chunks)
                    XCTAssertEqual(end.chunks, header.index)
                    XCTAssertEqual(Data(SHA256.hash(data: content)), bytes(end.sha256))
                case .ack:
                    let stage = try WireBody.decodeAck(body)
                    XCTAssertEqual(stage, directionSequence == 0 ? .ready : .complete)
                case .error, .drain:
                    XCTFail("Unexpected type in \(exchange.label) transfer: \(header.type)")
                }
            }
        }
    }

    func testSharedInvalidBodyFixtures() throws {
        let fixtures = try loadCorpus().negativeBodies
        XCTAssertFalse(fixtures.isEmpty)
        for fixture in fixtures {
            let body = bytes(fixture.body)
            switch try XCTUnwrap(FrameType(rawValue: fixture.type)) {
            case .open:
                for mode in ProtectionMode.allCases {
                    XCTAssertThrowsError(try WireBody.decodeOpen(body, mode: mode), fixture.label)
                }
            case .ack:
                XCTAssertThrowsError(try WireBody.decodeAck(body), fixture.label)
            default:
                XCTFail("Unrecognized malformed schema type: \(fixture.type)")
            }
        }
    }

    func testSharedBoundaryContents() throws {
        let corpus = try loadCorpus()
        let keys = try fixtureKeys().c2s
        XCTAssertEqual(Set(corpus.boundaryItems.map(\.size)), [0, 1, 65_535, 65_536, 65_537, 65_538])
        for fixture in corpus.boundaryItems {
            let original: Data
            switch fixture.pattern {
            case "byte[i] = i % 251":
                original = Data((0..<fixture.size).map { UInt8($0 % 251) })
            case "65535 ASCII a bytes followed by U+C548":
                original = Data(repeating: 0x61, count: 65_535) + Data("안".utf8)
            default:
                XCTFail("Unknown boundary fixture pattern: \(fixture.pattern)")
                continue
            }
            XCTAssertEqual(original.count, fixture.size)

            var lengths: [Int] = []
            var reassembled = Data()
            for start in stride(from: 0, to: original.count, by: Wire.chunkSize) {
                let index = UInt64(lengths.count)
                let content = original[start..<min(start + Wire.chunkSize, original.count)]
                let header = FrameHeader(
                    type: .data, mode: .aes256GCM, sequence: index, itemID: itemID, index: index, length: content.count
                )
                let record = try Wire.encode(header, body: content, keys: keys)
                let fixed = record.prefix(76)
                let decoded = try Wire.decodeHeader(fixed, keys: keys, expectedSequence: index)
                let body = try Wire.decodeBody(record.dropFirst(76), prefixAndHeaderAndMAC: fixed, header: decoded, keys: keys)
                lengths.append(body.count)
                reassembled.append(body)
            }
            XCTAssertEqual(lengths, fixture.chunks)
            XCTAssertEqual(reassembled, original)
            XCTAssertEqual(Data(SHA256.hash(data: reassembled)), bytes(fixture.sha256))
        }
    }

    func testPlaintextContentAndEndCanBothBeChangedWithoutChangingHeaderTags() throws {
        let keys = try fixtureKeys().c2s
        let initial = Data("abc".utf8)
        let altered = Data("abd".utf8)
        let end = try WireBody.end(
            bytes: 3, chunks: 1, sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
        let changedEnd = try WireBody.end(
            bytes: 3, chunks: 1, sha256: "a52d159f262b2c6ddb724a61840befc36eb30c88877a4030b65cbe86298449c9"
        )
        let dataHeader = FrameHeader(type: .data, mode: .plaintext, sequence: 0, itemID: itemID, index: 0, length: 3)
        let endHeader = FrameHeader(type: .end, mode: .plaintext, sequence: 1, itemID: itemID, index: 1, length: end.count)
        var dataRecord = try Wire.encode(dataHeader, body: initial, keys: keys)
        var endRecord = try Wire.encode(endHeader, body: end, keys: keys)

        dataRecord.replaceSubrange(76..<dataRecord.count, with: altered)
        endRecord.replaceSubrange(76..<endRecord.count, with: changedEnd)
        try assertDecoded(dataRecord, dataHeader, altered, keys)
        try assertDecoded(endRecord, endHeader, changedEnd, keys)
        let alteredHash = Data(SHA256.hash(data: altered))
        XCTAssertNotEqual(alteredHash, bytes(try WireBody.decodeEnd(end).sha256))
        XCTAssertEqual(alteredHash, bytes(try WireBody.decodeEnd(changedEnd).sha256))
    }

    private func loadCorpus() throws -> Corpus {
        let relativePath = "protocol/vectors/ulab1.json"
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        while true {
            let candidate = directory.appendingPathComponent(relativePath)
            if FileManager.default.fileExists(atPath: candidate.path) {
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase
                return try decoder.decode(Corpus.self, from: Data(contentsOf: candidate))
            }
            let parent = directory.deletingLastPathComponent()
            guard parent.path != directory.path else { break }
            directory = parent
        }
        throw CocoaError(.fileNoSuchFile)
    }

    private func handshake() throws -> (Data, Data, Data) {
        let ch = try Wire.makeClientHello(psk: psk, nonce: nc)
        let sh = try Wire.makeServerHello(clientHello: ch, psk: psk, nonce: ns)
        let cf = try Wire.makeClientFinish(clientHello: ch, serverHello: sh, psk: psk)
        return (ch, sh, cf)
    }

    private func fixtureKeys() throws -> (c2s: DirectionKeys, s2c: DirectionKeys) {
        let (ch, sh, cf) = try handshake()
        return try Wire.sessionKeys(clientHello: ch, serverHello: sh, clientFinish: cf, psk: psk)
    }

    private func dataHeader(mode: ProtectionMode) -> FrameHeader {
        FrameHeader(type: .data, mode: mode, sequence: 2, itemID: itemID, index: 0, length: message.count)
    }

    private func macHeader(_ prefixAndHeader: Data, _ key: SymmetricKey) -> Data {
        let mac = HMAC<SHA256>.authenticationCode(for: Data("ULAB1-HDR\0".utf8) + prefixAndHeader, using: key)
        return prefixAndHeader + Data(mac)
    }

    private func assertDecoded(_ record: Data, _ expected: FrameHeader, _ body: Data, _ keys: DirectionKeys) throws {
        let fixed = record.prefix(76)
        let header = try Wire.decodeHeader(fixed, keys: keys, expectedSequence: expected.sequence)
        let decoded = try Wire.decodeBody(record.dropFirst(76), prefixAndHeaderAndMAC: fixed, header: header, keys: keys)
        XCTAssertEqual(try Wire.prefixLength(record.prefix(4)), record.count - 4)
        XCTAssertEqual(header, expected)
        XCTAssertEqual(Wire.wireBodyLength(header), record.count - 76)
        XCTAssertEqual(decoded, body)
    }

    private func assertKey(_ key: SymmetricKey, _ expected: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(key.withUnsafeBytes { Data($0) }, bytes(expected), file: file, line: line)
    }

    private func flipped(_ input: Data, at offset: Int) -> Data {
        var bytes = Data(input)
        bytes[bytes.startIndex + offset] ^= 1
        return bytes
    }
}

private struct Corpus: Decodable {
    struct Handshake: Decodable {
        let psk: String
        let nc: String
        let ns: String
        let clientHello: String
        let serverHello: String
        let clientFinish: String
        let transcriptHash: String
        let keys: [String: [String: String]]
    }

    struct Exchange: Decodable {
        struct Record: Decodable {
            let direction: String
            let type: UInt8
            let sequence: UInt64
            let itemId: String
            let index: UInt64
            let body: String
            let record: String
        }

        let label: String
        let mode: UInt8
        let origin: String
        let records: [Record]
    }

    struct NegativeBody: Decodable {
        let label: String
        let type: UInt8
        let body: String
    }

    struct BoundaryItem: Decodable {
        let size: Int
        let pattern: String
        let sha256: String
        let chunks: [Int]
    }

    let handshake: Handshake
    let exchanges: [Exchange]
    let negativeBodies: [NegativeBody]
    let boundaryItems: [BoundaryItem]
}

private func bytes(_ hex: String) -> Data {
    var output = Data()
    var start = hex.startIndex
    while start < hex.endIndex {
        let end = hex.index(start, offsetBy: 2)
        output.append(UInt8(hex[start..<end], radix: 16)!)
        start = end
    }
    return output
}
