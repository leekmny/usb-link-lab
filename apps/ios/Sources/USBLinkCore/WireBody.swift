import Foundation

public enum ItemKind: String, Codable, Sendable {
    case text
    case image
    case file
}

public struct ItemMetadata: Codable, Equatable, Sendable {
    public let kind: ItemKind
    public let name: String
    public let mediaType: String
    public let size: UInt64
    public let chunkSize: UInt64

    public init(kind: ItemKind, name: String, mediaType: String, size: UInt64, chunkSize: UInt64 = 65_536) throws {
        guard chunkSize == UInt64(Wire.chunkSize) else { throw WireError.invalidMetadata }
        switch kind {
        case .text:
            guard name.isEmpty, mediaType == "text/plain; charset=utf-8", size <= 1_048_576 else {
                throw WireError.invalidMetadata
            }
        case .image, .file:
            guard Self.isSafeFilename(name), size <= Wire.maxFileSize,
                  (1...127).contains(mediaType.utf8.count),
                  mediaType.utf8.allSatisfy({ (0x20...0x7e).contains($0) }),
                  kind != .image || mediaType.hasPrefix("image/") else {
                throw WireError.invalidMetadata
            }
        }

        self.kind = kind
        self.name = name
        self.mediaType = mediaType
        self.size = size
        self.chunkSize = chunkSize
    }

    public static func decode(_ data: Data) throws -> ItemMetadata {
        let fields = try FlatObject(data, fields: ["kind", "name", "media_type", "size", "chunk_size"])
        guard let kind = ItemKind(rawValue: try fields.string("kind")) else {
            throw WireError.invalidMetadata
        }
        return try ItemMetadata(
            kind: kind, name: fields.string("name"), mediaType: fields.string("media_type"),
            size: fields.uint("size"), chunkSize: fields.uint("chunk_size")
        )
    }

    public func encode() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(self)
    }

    private enum CodingKeys: String, CodingKey {
        case kind, name, size
        case mediaType = "media_type"
        case chunkSize = "chunk_size"
    }

    // Codable supports local metadata records. Wire data always goes through decode(_:).
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(
            kind: values.decode(ItemKind.self, forKey: .kind),
            name: values.decode(String.self, forKey: .name),
            mediaType: values.decode(String.self, forKey: .mediaType),
            size: values.decode(UInt64.self, forKey: .size),
            chunkSize: values.decode(UInt64.self, forKey: .chunkSize)
        )
    }

    private static func isSafeFilename(_ name: String) -> Bool {
        // These display names must also pass the Windows companion's filename rules.
        guard (1...255).contains(name.utf8.count), name.utf8.last != 0x20, name.utf8.last != 0x2e else {
            return false
        }
        let forbidden = "<>:\"/\\|?*".unicodeScalars
        guard name.unicodeScalars.allSatisfy({
            !forbidden.contains($0) && $0.properties.generalCategory != .control
        }) else { return false }

        var stem = name.utf8.prefix { $0 != 0x2e }
        while stem.last == 0x20 {
            stem = stem.dropLast()
        }
        let upper = String(decoding: stem, as: UTF8.self).uppercased()
        if ["CON", "PRN", "AUX", "NUL"].contains(upper) {
            return false
        }
        if upper.count == 4, let digit = upper.last, "123456789¹²³".contains(digit) {
            let prefix = upper.dropLast()
            return prefix != "COM" && prefix != "LPT"
        }
        return true
    }
}

enum AckStage: String, Sendable {
    case ready, complete
}

enum DrainStage: String, Sendable {
    case request, ready
}

struct EndBody: Sendable {
    let bytes: UInt64
    let chunks: UInt64
    let sha256: String
}

enum WireBody {
    static func open(_ mode: ProtectionMode) -> Data {
        Data("{\"mode\":\(mode.rawValue)}".utf8)
    }

    static func decodeOpen(_ data: Data, mode: ProtectionMode) throws {
        let fields = try FlatObject(data, fields: ["mode"])
        guard try fields.uint("mode") == UInt64(mode.rawValue) else { throw WireError.invalidBody }
    }

    static func ack(_ stage: AckStage) -> Data {
        Data("{\"stage\":\"\(stage.rawValue)\"}".utf8)
    }

    static func decodeAck(_ data: Data) throws -> AckStage {
        let fields = try FlatObject(data, fields: ["stage"])
        guard let stage = AckStage(rawValue: try fields.string("stage")) else { throw WireError.invalidBody }
        return stage
    }

    static func end(bytes: UInt64, chunks: UInt64, sha256: String) throws -> Data {
        try validateEnd(bytes: bytes, chunks: chunks, sha256: sha256)
        return Data("{\"bytes\":\(bytes),\"chunks\":\(chunks),\"sha256\":\"\(sha256)\"}".utf8)
    }

    static func decodeEnd(_ data: Data) throws -> EndBody {
        let fields = try FlatObject(data, fields: ["bytes", "chunks", "sha256"])
        let end = try EndBody(bytes: fields.uint("bytes"), chunks: fields.uint("chunks"), sha256: fields.string("sha256"))
        try validateEnd(bytes: end.bytes, chunks: end.chunks, sha256: end.sha256)
        return end
    }

    static func drain(_ stage: DrainStage) -> Data {
        Data("{\"stage\":\"\(stage.rawValue)\"}".utf8)
    }

    static func decodeDrain(_ data: Data) throws -> DrainStage {
        let fields = try FlatObject(data, fields: ["stage"])
        guard let stage = DrainStage(rawValue: try fields.string("stage")) else { throw WireError.invalidBody }
        return stage
    }

    static let error = Data(#"{"code":"protocol_error"}"#.utf8)

    static func decodeError(_ data: Data) throws {
        let fields = try FlatObject(data, fields: ["code"])
        guard try fields.string("code") == "protocol_error" else { throw WireError.invalidBody }
    }

    private static func validateEnd(bytes: UInt64, chunks: UInt64, sha256: String) throws {
        guard bytes <= Wire.maxFileSize, chunks <= Wire.maxFileSize / UInt64(Wire.chunkSize),
              sha256.utf8.count == 64, sha256.utf8.allSatisfy({
                  (0x30...0x39).contains($0) || (0x61...0x66).contains($0)
              }) else { throw WireError.invalidBody }
    }
}

private struct FlatObject {
    private enum Value {
        case string(String)
        case uint(UInt64)
    }

    private let values: [String: Value]

    init(_ data: Data, fields: Set<String>) throws {
        guard data.count <= Wire.maxJSONSize else { throw WireError.invalidBody }
        var parser = Parser(bytes: Array(data))
        values = try parser.object(fields: fields)
    }

    func string(_ field: String) throws -> String {
        guard case let .string(value)? = values[field] else { throw WireError.invalidBody }
        return value
    }

    func uint(_ field: String) throws -> UInt64 {
        guard case let .uint(value)? = values[field] else { throw WireError.invalidBody }
        return value
    }

    // The wire grammar permits only one bounded object with string and uint64 values.
    // Parse member names after escapes, before inserting them, to catch duplicate keys.
    private struct Parser {
        let bytes: [UInt8]
        private var offset = 0

        init(bytes: [UInt8]) {
            self.bytes = bytes
        }

        private var current: UInt8? {
            offset < bytes.count ? bytes[offset] : nil
        }

        mutating func object(fields: Set<String>) throws -> [String: Value] {
            skipWhitespace()
            try expect(0x7b)
            skipWhitespace()
            var result: [String: Value] = [:]
            while current != 0x7d {
                let key = try string()
                guard fields.contains(key), result[key] == nil else { throw WireError.invalidBody }
                skipWhitespace()
                try expect(0x3a)
                skipWhitespace()
                if current == 0x22 {
                    result[key] = .string(try string())
                } else {
                    result[key] = .uint(try uint())
                }
                skipWhitespace()
                guard take(0x2c) else { break }
                skipWhitespace()
                guard current != 0x7d else { throw WireError.invalidBody }
            }
            try expect(0x7d)
            skipWhitespace()
            guard offset == bytes.count, result.count == fields.count else { throw WireError.invalidBody }
            return result
        }

        private mutating func skipWhitespace() {
            while current == 0x20 || current == 0x09 || current == 0x0a || current == 0x0d {
                offset += 1
            }
        }

        private mutating func take(_ byte: UInt8) -> Bool {
            guard current == byte else { return false }
            offset += 1
            return true
        }

        private mutating func expect(_ byte: UInt8) throws {
            guard take(byte) else { throw WireError.invalidBody }
        }

        private mutating func uint() throws -> UInt64 {
            let start = offset
            if take(0x30) { return 0 }
            while let byte = current, (0x30...0x39).contains(byte) {
                offset += 1
            }
            guard offset > start, let value = UInt64(String(decoding: bytes[start..<offset], as: UTF8.self)) else {
                throw WireError.invalidBody
            }
            return value
        }

        private mutating func string() throws -> String {
            try expect(0x22)
            var decoded: [UInt8] = []
            while let byte = current {
                offset += 1
                switch byte {
                case 0x22:
                    let result = String(decoding: decoded, as: UTF8.self)
                    // Decoding repairs malformed input. A byte round trip rejects those repairs on all target OS versions.
                    guard result.utf8.elementsEqual(decoded) else { throw WireError.invalidBody }
                    return result
                case 0x5c:
                    try appendEscape(to: &decoded)
                case 0x00...0x1f:
                    throw WireError.invalidBody
                default:
                    decoded.append(byte)
                }
            }
            throw WireError.invalidBody
        }

        private mutating func appendEscape(to decoded: inout [UInt8]) throws {
            guard let byte = current else { throw WireError.invalidBody }
            offset += 1
            switch byte {
            case 0x22, 0x2f, 0x5c: decoded.append(byte)
            case 0x62: decoded.append(0x08)
            case 0x66: decoded.append(0x0c)
            case 0x6e: decoded.append(0x0a)
            case 0x72: decoded.append(0x0d)
            case 0x74: decoded.append(0x09)
            case 0x75:
                var value = try hexCodeUnit()
                if (0xd800...0xdbff).contains(value) {
                    try expect(0x5c)
                    try expect(0x75)
                    let low = try hexCodeUnit()
                    guard (0xdc00...0xdfff).contains(low) else { throw WireError.invalidBody }
                    value = 0x10000 + ((value - 0xd800) << 10) + (low - 0xdc00)
                }
                guard let scalar = Unicode.Scalar(value) else { throw WireError.invalidBody }
                decoded.append(contentsOf: String(scalar).utf8)
            default:
                throw WireError.invalidBody
            }
        }

        private mutating func hexCodeUnit() throws -> UInt32 {
            var value: UInt32 = 0
            for _ in 0..<4 {
                guard let byte = current else { throw WireError.invalidBody }
                offset += 1
                let digit: UInt8
                switch byte {
                case 0x30...0x39: digit = byte - 0x30
                case 0x41...0x46: digit = byte - 0x41 + 10
                case 0x61...0x66: digit = byte - 0x61 + 10
                default: throw WireError.invalidBody
                }
                value = (value << 4) | UInt32(digit)
            }
            return value
        }
    }
}
