import CryptoKit
import Foundation
import ULABPairing

public enum PairingInvitation: Sendable {
    case qr(Data)
    case code(String)

    public enum Failure: Error {
        case invalidQR
        case invalidCode
    }

    public static func scanQR(_ value: String) throws -> Self {
        let prefix = "ULABQR1:".utf8
        let bytes = value.utf8
        guard bytes.count == prefix.count + 43, bytes.starts(with: prefix) else { throw Failure.invalidQR }
        let encoded = bytes.dropFirst(prefix.count)
        guard encoded.allSatisfy({ byte in
            switch byte {
            case 45, 48...57, 65...90, 95, 97...122: return true
            default: return false
            }
        }) else { throw Failure.invalidQR }

        let padded = String(decoding: encoded, as: UTF8.self)
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/") + "="
        guard let secret = Data(base64Encoded: padded), secret.base64EncodedString() == padded else {
            throw Failure.invalidQR
        }
        let invitation = Self.qr(secret)
        try invitation.validate()
        return invitation
    }

    public static func sixDigitCode(_ value: String) throws -> Self {
        try validateCode(value)
        return .code(value)
    }

    func validate() throws {
        switch self {
        case .qr(let secret):
            do { try PairingKeychain.validate(secret) }
            catch { throw Failure.invalidQR }
        case .code(let value):
            try Self.validateCode(value)
        }
    }

    fileprivate static func validateCode(_ value: String) throws {
        let bytes = value.utf8
        guard bytes.count == Int(ULAB_PAKE_CODE_LEN), bytes.allSatisfy({ (48...57).contains($0) }) else {
            throw Failure.invalidCode
        }
    }
}

enum EnrollmentWire {
    static let magic = Data("ULABP001".utf8)
    private static let headerSize = 11
    private static let maxShareSize = Int(ULAB_PAKE_MAX_MESSAGE)
    private static let keySize = Int(ULAB_PAKE_SECRET_LEN)

    struct Keys {
        let psk: SymmetricKey
        let client: SymmetricKey
        let server: SymmetricKey

        init(sharedSecret: SymmetricKey, transcript: Data) {
            func derive(_ purpose: String) -> SymmetricKey {
                HKDF<SHA256>.deriveKey(inputKeyMaterial: sharedSecret, salt: transcript,
                                      info: Data("ulab-ecjpake/1/\(purpose)".utf8), outputByteCount: keySize)
            }
            psk = derive("psk")
            client = derive("client-confirm")
            server = derive("server-confirm")
        }

        func acceptsClientTag(_ tag: Data, transcript: Data) -> Bool {
            HMAC<SHA256>.isValidAuthenticationCode(tag, authenticating: Data("ULABP001/C\0".utf8) + transcript,
                                                   using: client)
        }

        func serverTag(transcript: Data) -> Data {
            Data(HMAC<SHA256>.authenticationCode(for: Data("ULABP001/S\0".utf8) + transcript, using: server))
        }
    }

    // The session watchdog closes a blocked read or write at this same deadline.
    static func respond(on stream: any SessionStream, code: String, initialMagic: Data,
                        deadline: ContinuousClock.Instant) async throws -> Data {
        do {
            return try await withTaskCancellationHandler {
                try checkDeadline(deadline)
                guard initialMagic == magic else { throw SessionFault.protocolViolation }
                let (keys, transcript) = try await exchangeKeys(on: stream, code: code, initialMagic: initialMagic,
                                                               deadline: deadline)
                let confirmation = try await receive(on: stream, stage: 5, deadline: deadline)
                guard keys.acceptsClientTag(confirmation.body, transcript: transcript) else {
                    throw SessionFault.protocolViolation
                }
                let reply = try record(stage: 6, body: keys.serverTag(transcript: transcript))
                try checkDeadline(deadline)
                try await stream.write(reply)
                try checkDeadline(deadline)
                return keys.psk.withUnsafeBytes { Data($0) }
            } onCancel: {
                stream.close()
            }
        } catch {
            stream.close()
            try checkDeadline(deadline)
            throw error
        }
    }

    static func record(stage: UInt8, body: Data) throws -> Data {
        guard validLength(body.count, for: stage) else { throw SessionFault.protocolViolation }
        return magic + Data([stage, UInt8(body.count >> 8), UInt8(truncatingIfNeeded: body.count)]) + body
    }

    private static func exchangeKeys(on stream: any SessionStream, code: String, initialMagic: Data,
                                     deadline: ContinuousClock.Instant) async throws -> (Keys, Data) {
        let exchange = try NativeExchange(code: code)
        var hash = SHA256()
        for round in Int32(1)...2 {
            let stage = UInt8(2 * round - 1)
            let received = try await receive(on: stream, stage: stage, deadline: deadline,
                                             prefix: round == 1 ? initialMagic : nil)
            try exchange.read(round, body: received.body)
            let outgoing = try record(stage: stage + 1, body: exchange.write(round))
            try checkDeadline(deadline)
            try await stream.write(outgoing)
            try checkDeadline(deadline)
            hash.update(data: received.record)
            hash.update(data: outgoing)
        }
        let transcript = Data(hash.finalize())
        let keys = Keys(sharedSecret: try exchange.derive(), transcript: transcript)
        return (keys, transcript)
    }

    private static func receive(on stream: any SessionStream, stage: UInt8, deadline: ContinuousClock.Instant,
                                prefix: Data? = nil) async throws -> (record: Data, body: Data) {
        try checkDeadline(deadline)
        let readPrefix = prefix ?? Data()
        let header = readPrefix + (try await stream.exact(headerSize - readPrefix.count))
        try checkDeadline(deadline)
        let fields = header.suffix(3)
        let code = fields.startIndex
        let size = Int(fields[code + 1]) * 256 + Int(fields[code + 2])
        guard header.prefix(magic.count) == magic, fields[code] == stage, validLength(size, for: stage) else {
            throw SessionFault.protocolViolation
        }
        let body = try await stream.exact(size)
        try checkDeadline(deadline)
        return (header + body, body)
    }

    private static func validLength(_ size: Int, for stage: UInt8) -> Bool {
        switch stage {
        case 1...4: return (1...maxShareSize).contains(size)
        case 5, 6: return size == keySize
        default: return false
        }
    }

    private static func checkDeadline(_ deadline: ContinuousClock.Instant) throws {
        try Task.checkCancellation()
        guard ContinuousClock.now < deadline else { throw SessionFault.timeout }
    }

    private final class NativeExchange {
        private let handle: UnsafeMutableRawPointer

        init(code: String) throws {
            try PairingInvitation.validateCode(code)
            var bytes = Data(code.utf8)
            defer { bytes.resetBytes(in: 0..<bytes.count) }
            guard let handle = bytes.withUnsafeBytes({ (buffer: UnsafeRawBufferPointer) in
                ulab_pake_new(Int32(ULAB_PAKE_SERVER), buffer.bindMemory(to: UInt8.self).baseAddress, buffer.count)
            }) else { throw SessionFault.protocolViolation }
            self.handle = handle
        }

        deinit { ulab_pake_free(handle) }

        func read(_ round: Int32, body: Data) throws {
            let result = body.withUnsafeBytes { (buffer: UnsafeRawBufferPointer) in
                ulab_pake_read(handle, round, buffer.bindMemory(to: UInt8.self).baseAddress, buffer.count)
            }
            guard result == 0 else { throw SessionFault.protocolViolation }
        }

        func write(_ round: Int32) throws -> Data {
            var bytes = [UInt8](repeating: 0, count: maxShareSize)
            var written = 0
            let result = bytes.withUnsafeMutableBufferPointer {
                ulab_pake_write(handle, round, $0.baseAddress, $0.count, &written)
            }
            guard result == 0, (1...maxShareSize).contains(written) else { throw SessionFault.protocolViolation }
            return Data(bytes.prefix(written))
        }

        func derive() throws -> SymmetricKey {
            var secret = Data(count: keySize)
            defer { secret.resetBytes(in: 0..<keySize) }
            let result = secret.withUnsafeMutableBytes { (buffer: UnsafeMutableRawBufferPointer) in
                ulab_pake_derive(handle, buffer.bindMemory(to: UInt8.self).baseAddress)
            }
            guard result == 0 else { throw SessionFault.protocolViolation }
            return SymmetricKey(data: secret)
        }
    }
}
