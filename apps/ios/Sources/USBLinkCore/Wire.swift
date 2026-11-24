import CryptoKit
import Foundation

public enum ProtectionMode: UInt8, Codable, CaseIterable, Sendable {
    case plaintext = 0
    case aes256GCM = 1
    case chacha20Poly1305 = 2
    case hmacSHA256 = 3

    var overhead: Int {
        switch self {
        case .plaintext: return 0
        case .aes256GCM, .chacha20Poly1305: return 16
        case .hmacSHA256: return 32
        }
    }
}

enum FrameType: UInt8, Sendable {
    case open = 1
    case meta = 2
    case data = 3
    case end = 4
    case ack = 5
    case error = 6
    case drain = 7

    var isControl: Bool {
        switch self {
        case .open, .ack, .error, .drain: return true
        case .meta, .data, .end: return false
        }
    }
}

struct FrameHeader: Equatable, Sendable {
    let type: FrameType
    let mode: ProtectionMode
    let sequence: UInt64
    let itemID: Data
    let index: UInt64
    let length: Int
}

struct DirectionKeys: Sendable {
    let hdr: SymmetricKey
    let ctl: SymmetricKey
    let aes: SymmetricKey
    let chacha: SymmetricKey
    let hmac: SymmetricKey
}

enum WireError: Error, Equatable {
    case invalidHandshake
    case invalidFrameLength
    case invalidHeader
    case unexpectedSequence
    case authenticationFailed
    case invalidBody
    case invalidMetadata
}

enum Wire {
    static let handshakeHelloSize = 73
    static let handshakeFinishSize = 41
    static let prefixSize = 4
    static let authenticatedHeaderSize = 76
    static let chunkSize = 65_536
    static let maxJSONSize = 4_096
    static let maxFileSize: UInt64 = 1_073_741_824

    private static let magic = Data("ULAB0001".utf8)
    private static let headerAndTagSize = 72
    private static let prefixAndHeaderSize = 44
    private static let maxFrameLength = 1_048_576
    private static let maxSequence = UInt64(UInt32.max)
    private static let headerDomain = Data("ULAB1-HDR\0".utf8)
    private static let controlDomain = Data("ULAB1-CTL\0".utf8)
    private static let payloadDomain = Data("ULAB1-PAYLOAD\0".utf8)

    static func makeClientHello(psk: Data, nonce: Data) throws -> Data {
        guard psk.count == 32, nonce.count == 32 else { throw WireError.invalidHandshake }
        let record = magic + Data([1]) + nonce
        let key = SymmetricKey(data: psk)
        return record + mac(Data("ULAB1-CH\0".utf8) + record, key: key)
    }

    static func makeServerHello(clientHello: Data, psk: Data, nonce: Data) throws -> Data {
        guard psk.count == 32, nonce.count == 32 else { throw WireError.invalidHandshake }
        let key = SymmetricKey(data: psk)
        try verifyClientHello(clientHello, key: key)
        let record = magic + Data([2]) + nonce
        return record + mac(Data("ULAB1-SH\0".utf8) + clientHello + record, key: key)
    }

    static func makeClientFinish(clientHello: Data, serverHello: Data, psk: Data) throws -> Data {
        guard psk.count == 32 else { throw WireError.invalidHandshake }
        let key = SymmetricKey(data: psk)
        try verifyHellos(clientHello: clientHello, serverHello: serverHello, key: key)
        let record = magic + Data([3])
        return record + mac(Data("ULAB1-CF\0".utf8) + clientHello + serverHello + record, key: key)
    }

    static func sessionKeys(
        clientHello: Data, serverHello: Data, clientFinish: Data, psk: Data
    ) throws -> (c2s: DirectionKeys, s2c: DirectionKeys) {
        guard psk.count == 32 else { throw WireError.invalidHandshake }
        let key = SymmetricKey(data: psk)
        try verifyHellos(clientHello: clientHello, serverHello: serverHello, key: key)
        try verifyHandshakeRecord(
            clientFinish, kind: 3, length: handshakeFinishSize,
            authenticatedPrefix: Data("ULAB1-CF\0".utf8) + clientHello + serverHello, key: key
        )
        let transcriptHash = Data(SHA256.hash(data: clientHello + serverHello + clientFinish))
        func derive(_ direction: String) -> DirectionKeys {
            func keyFor(_ purpose: String) -> SymmetricKey {
                HKDF<SHA256>.deriveKey(
                    inputKeyMaterial: key, salt: transcriptHash,
                    info: Data("ULAB1/\(direction)/\(purpose)".utf8), outputByteCount: 32
                )
            }
            return DirectionKeys(hdr: keyFor("hdr"), ctl: keyFor("ctl"), aes: keyFor("aes"),
                                 chacha: keyFor("chacha"), hmac: keyFor("hmac"))
        }
        return (c2s: derive("c2s"), s2c: derive("s2c"))
    }

    static func prefixLength(_ prefix: Data) throws -> Int {
        guard prefix.count == prefixSize else { throw WireError.invalidFrameLength }
        let length = Int(unsigned(prefix))
        guard (headerAndTagSize...maxFrameLength).contains(length) else {
            throw WireError.invalidFrameLength
        }
        return length
    }

    static func decodeHeader(
        _ prefixAndHeaderAndMAC: Data, keys: DirectionKeys, expectedSequence: UInt64
    ) throws -> FrameHeader {
        guard prefixAndHeaderAndMAC.count == authenticatedHeaderSize else {
            throw WireError.invalidFrameLength
        }
        let length = try prefixLength(prefixAndHeaderAndMAC.prefix(prefixSize))
        let prefixAndHeader = prefixAndHeaderAndMAC.prefix(prefixAndHeaderSize)
        try verifyMAC(prefixAndHeaderAndMAC.suffix(32), for: headerDomain + prefixAndHeader, key: keys.hdr)

        let raw = Array(prefixAndHeader.dropFirst(prefixSize))
        guard raw[0] == 1, raw[3] == 0,
              let type = FrameType(rawValue: raw[1]),
              let mode = ProtectionMode(rawValue: raw[2]) else {
            throw WireError.invalidHeader
        }
        let header = FrameHeader(
            type: type, mode: mode, sequence: unsigned(raw[4..<12]), itemID: Data(raw[12..<28]),
            index: unsigned(raw[28..<36]), length: Int(unsigned(raw[36..<40]))
        )
        try validate(header)
        guard header.sequence == expectedSequence else { throw WireError.unexpectedSequence }
        guard length == headerAndTagSize + wireBodyLength(header) else {
            throw WireError.invalidFrameLength
        }
        return header
    }

    static func wireBodyLength(_ header: FrameHeader) -> Int {
        header.length + (header.type.isControl ? 32 : header.mode.overhead)
    }

    // The caller uses this only after decodeHeader has authenticated these exact header bytes.
    static func decodeBody(
        _ bytes: Data, prefixAndHeaderAndMAC: Data, header: FrameHeader, keys: DirectionKeys
    ) throws -> Data {
        try validate(header)
        guard prefixAndHeaderAndMAC.count == authenticatedHeaderSize, bytes.count == wireBodyLength(header) else {
            throw WireError.invalidFrameLength
        }
        let prefixAndHeader = prefixAndHeaderAndMAC.prefix(prefixAndHeaderSize)
        if header.type.isControl {
            return try decodeMACBody(bytes, authenticatedPrefix: controlDomain + prefixAndHeader, key: keys.ctl)
        }
        let aad = payloadDomain + prefixAndHeader
        let nonce = Data(repeating: 0, count: 4) + bigEndian(header.sequence)
        switch header.mode {
        case .plaintext:
            return Data(bytes)
        case .hmacSHA256:
            return try decodeMACBody(bytes, authenticatedPrefix: aad, key: keys.hmac)
        case .aes256GCM:
            do {
                let sealed = try AES.GCM.SealedBox(
                    nonce: AES.GCM.Nonce(data: nonce), ciphertext: bytes.prefix(header.length), tag: bytes.suffix(16)
                )
                return try AES.GCM.open(sealed, using: keys.aes, authenticating: aad)
            } catch {
                throw WireError.authenticationFailed
            }
        case .chacha20Poly1305:
            do {
                let sealed = try ChaChaPoly.SealedBox(
                    nonce: ChaChaPoly.Nonce(data: nonce), ciphertext: bytes.prefix(header.length), tag: bytes.suffix(16)
                )
                return try ChaChaPoly.open(sealed, using: keys.chacha, authenticating: aad)
            } catch {
                throw WireError.authenticationFailed
            }
        }
    }

    static func encode(_ header: FrameHeader, body: Data, keys: DirectionKeys) throws -> Data {
        try validate(header)
        guard body.count == header.length else { throw WireError.invalidFrameLength }
        var prefixAndHeader = bigEndian(UInt32(headerAndTagSize + wireBodyLength(header)))
        prefixAndHeader.append(contentsOf: [1, header.type.rawValue, header.mode.rawValue, 0])
        prefixAndHeader.append(bigEndian(header.sequence))
        prefixAndHeader.append(header.itemID)
        prefixAndHeader.append(bigEndian(header.index))
        prefixAndHeader.append(bigEndian(UInt32(header.length)))

        let authenticatedHeader = prefixAndHeader + mac(headerDomain + prefixAndHeader, key: keys.hdr)
        if header.type.isControl {
            return authenticatedHeader + body + mac(controlDomain + prefixAndHeader + body, key: keys.ctl)
        }

        let aad = payloadDomain + prefixAndHeader
        let nonce = Data(repeating: 0, count: 4) + bigEndian(header.sequence)
        switch header.mode {
        case .plaintext:
            return authenticatedHeader + body
        case .hmacSHA256:
            return authenticatedHeader + body + mac(aad + body, key: keys.hmac)
        case .aes256GCM:
            let sealed = try AES.GCM.seal(body, using: keys.aes, nonce: AES.GCM.Nonce(data: nonce), authenticating: aad)
            return authenticatedHeader + sealed.ciphertext + sealed.tag
        case .chacha20Poly1305:
            let sealed = try ChaChaPoly.seal(
                body, using: keys.chacha, nonce: ChaChaPoly.Nonce(data: nonce), authenticating: aad
            )
            return authenticatedHeader + sealed.ciphertext + sealed.tag
        }
    }

    private static func validate(_ header: FrameHeader) throws {
        guard header.sequence <= maxSequence else { throw WireError.unexpectedSequence }
        guard header.itemID.count == 16 else { throw WireError.invalidHeader }
        let zeroID = header.itemID.allSatisfy { $0 == 0 }
        switch header.type {
        case .error, .drain:
            guard zeroID, header.mode == .plaintext, header.index == 0 else { throw WireError.invalidHeader }
        case .open, .meta, .ack:
            guard !zeroID, header.index == 0 else { throw WireError.invalidHeader }
        case .data, .end:
            guard !zeroID else { throw WireError.invalidHeader }
        }
        let allowed = header.type == .data ? 1...chunkSize : 0...maxJSONSize
        guard allowed.contains(header.length) else { throw WireError.invalidFrameLength }
    }

    private static func verifyClientHello(_ record: Data, key: SymmetricKey) throws {
        try verifyHandshakeRecord(
            record, kind: 1, length: handshakeHelloSize, authenticatedPrefix: Data("ULAB1-CH\0".utf8), key: key
        )
    }

    private static func verifyHellos(clientHello: Data, serverHello: Data, key: SymmetricKey) throws {
        try verifyClientHello(clientHello, key: key)
        try verifyHandshakeRecord(
            serverHello, kind: 2, length: handshakeHelloSize,
            authenticatedPrefix: Data("ULAB1-SH\0".utf8) + clientHello, key: key
        )
    }

    private static func verifyHandshakeRecord(
        _ record: Data, kind: UInt8, length: Int, authenticatedPrefix: Data, key: SymmetricKey
    ) throws {
        guard record.count == length, record.prefix(9).elementsEqual(magic + Data([kind])) else {
            throw WireError.invalidHandshake
        }
        try verifyMAC(record.suffix(32), for: authenticatedPrefix + record.dropLast(32), key: key)
    }

    private static func decodeMACBody(_ bytes: Data, authenticatedPrefix: Data, key: SymmetricKey) throws -> Data {
        let body = Data(bytes.dropLast(32))
        try verifyMAC(bytes.suffix(32), for: authenticatedPrefix + body, key: key)
        return body
    }

    private static func mac(_ input: Data, key: SymmetricKey) -> Data {
        Data(HMAC<SHA256>.authenticationCode(for: input, using: key))
    }

    private static func verifyMAC(_ tag: Data, for input: Data, key: SymmetricKey) throws {
        guard HMAC<SHA256>.isValidAuthenticationCode(tag, authenticating: input, using: key) else {
            throw WireError.authenticationFailed
        }
    }

    private static func unsigned<Bytes: Sequence>(_ bytes: Bytes) -> UInt64 where Bytes.Element == UInt8 {
        bytes.reduce(UInt64(0)) { ($0 << 8) | UInt64($1) }
    }

    private static func bigEndian<T: FixedWidthInteger>(_ value: T) -> Data {
        var encoded = value.bigEndian
        return withUnsafeBytes(of: &encoded) { Data($0) }
    }
}
