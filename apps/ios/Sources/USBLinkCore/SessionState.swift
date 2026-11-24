import Foundation
import CryptoKit

public enum TransferDirection: String, Codable, Sendable { case incoming, outgoing }
public enum TransferState: String, Codable, Sendable {
    case queued, sending, receiving, completed, interrupted, unconfirmed
}

public struct TransferRecord: Identifiable, Sendable {
    public let id: UUID
    public let direction: TransferDirection
    public let mode: ProtectionMode
    public let metadata: ItemMetadata?
    public let state: TransferState
    public let bytes: UInt64
    public let detail: String
    public let fileURL: URL?

    public init(id: UUID, direction: TransferDirection, mode: ProtectionMode, metadata: ItemMetadata?,
                state: TransferState, bytes: UInt64, detail: String, fileURL: URL?) {
        self.id = id
        self.direction = direction
        self.mode = mode
        self.metadata = metadata
        self.state = state
        self.bytes = bytes
        self.detail = detail
        self.fileURL = fileURL
    }
}

public enum PairingStatus: Equatable, Sendable {
    case idle, ready, connecting, expired, failed, paired
}

public struct LabSnapshot: Sendable {
    public let status: String
    public let paired: Bool
    public let paused: Bool
    public let connected: Bool
    public let records: [TransferRecord]
    public var pairingStatus: PairingStatus = .idle
    public var pairingExpiresAt: Date?
}

struct JournalEntry: Codable {
    let id: UUID
    let direction: TransferDirection
    let mode: ProtectionMode
    let peer: Data?
    var order: Date
    var metadata: ItemMetadata?
    var state: TransferState
    var bytes: UInt64 = 0
    var detail: String = ""
    // Written to disk before END can reach the peer.
    var endPending = false
}

struct QueueJournal: Codable {
    var entries: [JournalEntry] = []
    var paused = false
}

struct SessionBudget {
    static let limit: UInt64 = 1 << 32
    static let idLimit = 4096
    var frames: UInt64 = 0
    var bytes: UInt64 = 0

    func canAdmit(size: UInt64, localIDs: Int, peerCompletions: Int) -> Bool {
        guard localIDs < Self.idLimit, (0...Self.idLimit).contains(peerCompletions),
              size <= 1_073_741_824, frames <= Self.limit, bytes <= Self.limit else { return false }
        let remaining = UInt64(Self.idLimit - peerCompletions)
        let chunks = (size + 65535) / 65536
        return size + 6 * 4096 + 2 * remaining * 4096 <= Self.limit - bytes &&
            chunks + 6 + 2 * remaining <= Self.limit - frames
    }

    mutating func charge(_ length: Int) throws {
        guard (0...65536).contains(length), frames < Self.limit,
              bytes <= Self.limit - UInt64(length) else { throw SessionFault.protocolViolation }
        frames += 1
        bytes += UInt64(length)
    }
}

// All mutable state below belongs to USBServer's actor executor.
final class SendingItem {
    enum Stage { case waitingReady, sending, waitingComplete }
    let local: UUID
    let wire: Data
    let mode: ProtectionMode
    let metadata: ItemMetadata
    var stage = Stage.waitingReady
    var waitingFrameDispatched = false
    var deadline = ContinuousClock.now.advanced(by: .seconds(60))

    init(local: UUID, wire: Data, mode: ProtectionMode, metadata: ItemMetadata) {
        self.local = local
        self.wire = wire
        self.mode = mode
        self.metadata = metadata
    }
}

final class ReceivingItem {
    let local = UUID()
    let wire: Data
    let mode: ProtectionMode
    var metadata: ItemMetadata?
    var sink: IncomingSink?
    var hash = SHA256()
    var bytes: UInt64 = 0
    var chunks: UInt64 = 0
    var committed = false
    var deadline = ContinuousClock.now.advanced(by: .seconds(60))

    init(wire: Data, mode: ProtectionMode) {
        self.wire = wire
        self.mode = mode
    }
}

final class LiveSession {
    let stream: SessionStream
    var peer: Data?
    var keys: (c2s: DirectionKeys, s2c: DirectionKeys)?
    var sendBudget = SessionBudget()
    var receiveBudget = SessionBudget()
    var localIDs = Set<Data>()
    var peerIDs = Set<Data>()
    var peerCompletions = 0
    var outgoing: SendingItem?
    var incoming: ReceivingItem?
    var writerTail: Task<Void, Error>?
    var reader: Task<Void, Never>?
    var watchdog: Task<Void, Never>?
    var watchdogAt: ContinuousClock.Instant?
    var handshakeDeadline: ContinuousClock.Instant? = .now.advanced(by: .seconds(10))
    var frameDeadline: ContinuousClock.Instant?
    var drainDeadline: ContinuousClock.Instant?
    var requestEnqueued = false
    var requestDispatched = false
    var requestWritten = false
    var requestReceived = false
    var readyEnqueued = false
    var readyWritten = false
    var readyReceived = false

    init(stream: SessionStream) {
        self.stream = stream
    }

    var drained: Bool { readyWritten && readyReceived }
    var nextDeadline: ContinuousClock.Instant? {
        [handshakeDeadline, frameDeadline, outgoing?.deadline, incoming?.deadline, drainDeadline]
            .compactMap { $0 }.min()
    }

    var expired: Bool { nextDeadline.map { $0 <= .now } ?? false }
}

func randomBytes(_ count: Int) -> Data {
    var generator = SystemRandomNumberGenerator()
    return Data((0..<count).map { _ in UInt8.random(in: .min ... .max, using: &generator) })
}

func digestString(_ digest: SHA256.Digest) -> String {
    digest.map { String(format: "%02x", $0) }.joined()
}
