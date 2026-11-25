import Foundation
import SwiftUI
import USBLinkCore

enum LabLimits {
    static let text = 1_048_576
    static let attachment: UInt64 = 1_073_741_824
}

extension ProtectionMode {
    static var choices: [Self] { [.plaintext, .aes256GCM, .chacha20Poly1305, .hmacSHA256] }

    var title: String {
        switch self {
        case .plaintext: "Plaintext"
        case .aes256GCM: "AES-256-GCM"
        case .chacha20Poly1305: "ChaCha20-Poly1305"
        case .hmacSHA256: "HMAC-SHA256"
        }
    }

    var symbol: String {
        switch self {
        case .plaintext: "eye"
        case .hmacSHA256: "checkmark.shield"
        case .aes256GCM, .chacha20Poly1305: "lock.shield"
        }
    }

    var confidential: Bool { self == .aes256GCM || self == .chacha20Poly1305 }
    var authentic: Bool { self != .plaintext }

    var exposure: LabCopy {
        switch self {
        case .plaintext: .plaintextExposure
        case .hmacSHA256: .authenticatedExposure
        case .aes256GCM, .chacha20Poly1305: .encryptedExposure
        }
    }
}

struct LabItem: Identifiable {
    let id: UUID
    let direction: TransferDirection
    let mode: ProtectionMode
    let kind: ItemKind?
    let name: String?
    let mediaType: String?
    let total: UInt64?
    let state: TransferState
    let bytes: UInt64
    let detail: String
    let fileURL: URL?

    init(record: TransferRecord) {
        id = record.id
        direction = record.direction
        mode = record.mode
        kind = record.metadata?.kind
        name = record.metadata?.name
        mediaType = record.metadata?.mediaType
        total = record.metadata?.size
        state = record.state
        bytes = record.bytes
        detail = record.detail
        fileURL = record.fileURL
    }

    #if DEBUG && targetEnvironment(simulator)
    init(id: UUID, direction: TransferDirection, mode: ProtectionMode, kind: ItemKind, name: String, mediaType: String, total: UInt64, state: TransferState, bytes: UInt64, detail: String = "") {
        self.id = id
        self.direction = direction
        self.mode = mode
        self.kind = kind
        self.name = name
        self.mediaType = mediaType
        self.total = total
        self.state = state
        self.bytes = bytes
        self.detail = detail
        fileURL = nil
    }
    #endif

    var active: Bool { state == .sending || state == .receiving }
    var needsAttention: Bool { state == .unconfirmed || state == .interrupted }
    var canRetry: Bool { direction == .outgoing && needsAttention }
    var canDeleteReceivedMessage: Bool { direction == .incoming && state == .completed && kind == .text }
    var downloadable: Bool { direction == .incoming && state == .completed && kind != .text && fileURL != nil }

    var status: LabCopy {
        switch state {
        case .queued: .queued
        case .sending: .sending
        case .receiving: .receiving
        case .completed: direction == .incoming ? .checked : .delivered
        case .interrupted: .interrupted
        case .unconfirmed: .unconfirmed
        }
    }

    var kindWord: LabCopy {
        switch kind {
        case .text: .message
        case .image: .image
        case .file: .genericFile
        case nil: .detailsUnknown
        }
    }

    var symbol: String {
        switch kind {
        case .text: "text.bubble"
        case .image: "photo"
        case .file: "doc"
        case nil: "arrow.down.document"
        }
    }

    func displayName(_ language: LabLanguage) -> String {
        if let name, !name.isEmpty { return name }
        return language.text(kindWord)
    }
}

struct LabConnection {
    var code = "starting"
    var paired = false
    var paused = false
    var connected = false
    var pairingStatus: PairingStatus = .idle
    var pairingExpiresAt: Date?

    var label: LabCopy {
        if connected { return .linked }
        switch code {
        case "starting": return .starting
        case "handshake": return .handshake
        case "listener_failed", "storage_error": return .unavailable
        case "connection_closed", "protocol_error", "reconnect_waiting", "timeout", "retrying", "restart": return .reconnecting
        default: return paired ? .waiting : .unpaired
        }
    }

    var message: LabCopy {
        if let recovery = Self.recovery(code) { return recovery }
        if connected { return .linkedInstruction }
        return paired ? .hostInstruction : .firstPairingInstruction
    }

    static func recovery(_ code: String) -> LabCopy? {
        switch code {
        case "background": .background
        case "listener_failed": .listenerFailed
        case "protocol_error": .protocolFailed
        case "connection_closed", "reconnect_waiting": .reconnectInstruction
        case "restart": .disconnected
        case "timeout": .timeout
        case "storage_error": .storage
        case "cancelled", "paused": .pausedInstruction
        case "retrying": .retryNotice
        case "pairing_changed": .pairingMismatch
        case "draining": .draining
        default: nil
        }
    }
}

struct LabProblem: Identifiable {
    let id = UUID()
    let message: LabCopy
}

@MainActor
final class LabModel: ObservableObject {
    @Published private(set) var connection = LabConnection()
    @Published private(set) var items: [LabItem] = []
    @Published private(set) var ready = false
    @Published private(set) var busy = false
    @Published var problem: LabProblem?

    private var server: USBServer?
    private var foreground = false
    let synthetic: Bool

    init() {
        #if DEBUG && targetEnvironment(simulator)
        synthetic = ProcessInfo.processInfo.arguments.contains("--lab-synthetic")
        if synthetic { populateExample() }
        #else
        synthetic = false
        #endif
    }

    func connect() async {
        guard !synthetic, server == nil else { return }
        do {
            let backend = try await Task.detached(priority: .userInitiated) {
                let directory = try FileManager.default.url(
                    for: .applicationSupportDirectory,
                    in: .userDomainMask,
                    appropriateFor: nil,
                    create: true
                ).appendingPathComponent("USBLinkLab", isDirectory: true)
                AttachmentFiles.purgeStaging()
                return try USBServer(root: directory)
            }.value
            guard !Task.isCancelled else { return }
            server = backend
            let changes = await backend.changes()
            await backend.setForeground(foreground)
            ready = true
            for await value in changes {
                connection = LabConnection(
                    code: value.status,
                    paired: value.paired,
                    paused: value.paused,
                    connected: value.connected,
                    pairingStatus: value.pairingStatus,
                    pairingExpiresAt: value.pairingExpiresAt
                )
                items = value.records.map(LabItem.init(record:))
            }
            await backend.setForeground(false)
            server = nil
            ready = false
        } catch {
            connection.code = "storage_error"
            report(.storage)
        }
    }

    func setForeground(_ value: Bool) async {
        foreground = value
        if synthetic && !value { await cancelPairing() }
        await server?.setForeground(value)
    }

    func report(_ message: LabCopy) {
        problem = LabProblem(message: message)
    }

    func enqueueText(_ text: String, mode: ProtectionMode) async -> Bool {
        await perform(or: .queueFailed) { server in
            try await server.enqueueText(text, mode: mode)
        }
    }

    func enqueueAttachment(_ file: PreparedAttachment, mode: ProtectionMode) async -> Bool {
        await perform(or: .queueFailed, reportFailure: false) { server in
            try await server.enqueueAttachment(file.url, kind: file.kind, name: file.name, mediaType: file.mediaType, mode: mode)
        }
    }

    func preparePairing(_ invitation: PairingInvitation) async -> Bool {
        guard foreground, !Task.isCancelled, !busy else { return false }
        #if DEBUG && targetEnvironment(simulator)
        if synthetic {
            connection.pairingStatus = .ready
            connection.pairingExpiresAt = Date().addingTimeInterval(120)
            return true
        }
        #endif
        guard let server else { return false }
        busy = true
        defer { busy = false }
        do {
            try await server.preparePairing(invitation)
            if Task.isCancelled || !foreground {
                await server.cancelPairing()
                return false
            }
            return true
        } catch {
            return false
        }
    }

    func cancelPairing() async {
        #if DEBUG && targetEnvironment(simulator)
        if synthetic {
            connection.pairingStatus = .idle
            connection.pairingExpiresAt = nil
            return
        }
        #endif
        await server?.cancelPairing()
    }

    func forgetPairing() async {
        _ = await perform(or: .forgetPairingFailed) { server in
            try await server.forgetPairing()
        }
    }

    func cancelActive() async {
        if synthetic {
            report(.testOnly)
        } else if let server {
            await server.cancelActive()
        } else {
            report(.operationFailed)
        }
    }

    func remove(_ id: UUID) async -> Bool {
        await perform(or: .operationFailed) { server in
            try await server.removeQueued(id)
        }
    }

    func deleteReceivedMessage(_ id: UUID) async -> Bool {
        #if DEBUG && targetEnvironment(simulator)
        if synthetic {
            guard let index = items.firstIndex(where: { $0.id == id && $0.canDeleteReceivedMessage }) else { return false }
            items.remove(at: index)
            return true
        }
        #endif
        return await perform(or: .operationFailed) { server in
            try await server.deleteReceivedMessage(id)
        }
    }

    func deleteReceivedMessages(_ ids: Set<UUID>) async -> Bool {
        #if DEBUG && targetEnvironment(simulator)
        if synthetic {
            let eligible = Set(items.lazy.filter(\.canDeleteReceivedMessage).map(\.id))
            guard !ids.isEmpty, ids.isSubset(of: eligible) else { return false }
            items.removeAll { ids.contains($0.id) }
            return true
        }
        #endif
        return await perform(or: .operationFailed) { server in
            try await server.deleteReceivedMessages(ids)
        }
    }

    func retry(_ id: UUID) async -> Bool {
        await perform(or: .operationFailed) { server in
            try await server.retry(id)
        }
    }

    func resume() async {
        _ = await perform(or: .resumeFailed) { server in
            await server.resume()
        }
    }

    private func perform(
        or failure: LabCopy,
        reportFailure: Bool = true,
        work: (USBServer) async throws -> Void
    ) async -> Bool {
        if synthetic {
            if reportFailure { report(.testOnly) }
            return false
        }
        guard let server, !busy else {
            if reportFailure { report(failure) }
            return false
        }
        busy = true
        defer { busy = false }
        do {
            try await work(server)
            return true
        } catch {
            guard reportFailure else { return false }
            let message: LabCopy
            switch (error as? LocalizedError)?.errorDescription {
            case "queue_full": message = .queueFull
            case "unpaired": message = .mustPair
            case "pairing_changed": message = .pairingMismatch
            case "storage_error": message = .storage
            default: message = failure
            }
            report(message)
            return false
        }
    }

    #if DEBUG && targetEnvironment(simulator)
    private func populateExample() {
        ready = true
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--lab-state=transfer") {
            connection = LabConnection(code: "connected", paired: true, paused: false, connected: true)
            items = [
                LabItem(id: UUID(), direction: .outgoing, mode: .aes256GCM, kind: .image, name: "color-target.png", mediaType: "image/png", total: 2_097_152, state: .sending, bytes: 1_245_184),
                LabItem(id: UUID(), direction: .incoming, mode: .hmacSHA256, kind: .file, name: "reference.txt", mediaType: "text/plain", total: 286_720, state: .receiving, bytes: 65_536)
            ]
        } else if arguments.contains("--lab-state=received") {
            items = [
                LabItem(id: UUID(), direction: .incoming, mode: .aes256GCM, kind: .text,
                        name: "", mediaType: "text/plain; charset=utf-8", total: 4, state: .completed, bytes: 4)
            ]
        } else if arguments.contains("--lab-state=received-multiple") {
            items = ["message-gamma.txt", "message-beta.txt", "message-alpha.txt"].map { name in
                LabItem(id: UUID(), direction: .incoming, mode: .aes256GCM, kind: .text,
                        name: name, mediaType: "text/plain; charset=utf-8", total: 4, state: .completed, bytes: 4)
            }
            items.insert(contentsOf: [
                LabItem(id: UUID(), direction: .outgoing, mode: .aes256GCM, kind: .text,
                        name: "sent-message.txt", mediaType: "text/plain; charset=utf-8", total: 4, state: .completed, bytes: 4),
                LabItem(id: UUID(), direction: .incoming, mode: .aes256GCM, kind: .file,
                        name: "received-note.txt", mediaType: "text/plain", total: 4, state: .completed, bytes: 4)
            ], at: 0)
        } else if arguments.contains("--lab-state=failure") {
            connection = LabConnection(code: "connection_closed", paired: true, paused: true, connected: false)
            items = [
                LabItem(id: UUID(), direction: .outgoing, mode: .chacha20Poly1305, kind: .file, name: "test-pattern.bin", mediaType: "application/octet-stream", total: 458_752, state: .unconfirmed, bytes: 458_752, detail: "connection_closed")
            ]
        } else {
            connection = LabConnection(code: "unpaired", paired: false, paused: false, connected: false)
        }
    }
    #endif
}
