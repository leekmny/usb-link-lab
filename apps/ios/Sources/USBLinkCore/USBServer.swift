import Foundation
import CryptoKit

public actor USBServer {
    let store: LabStore
    private let journalURL: URL
    let usesSystemServices: Bool
    var journal: QueueJournal
    var receivedFiles: [UUID: URL] = [:]
    var secret: Data?
    var session: LiveSession?
    private var listener: LoopbackListener?
    private var listenerID: UUID?
    private var pendingReconnect: (stream: SessionStream, deadline: ContinuousClock.Instant, timer: Task<Void, Never>)?
    private var foreground = false
    private var staging = Set<UUID>()
    private var observers: [UUID: AsyncStream<LabSnapshot>.Continuation] = [:]
    private var lastProgress = ContinuousClock.now
    var status = "background"

    public init(root: URL) throws {
        try self.init(root: root, secret: PairingKeychain.load(), usesSystemServices: true)
    }

    // Tests use temporary directories, an in-memory pairing, and an injected byte stream.
    init(root: URL, secret: Data?, usesSystemServices: Bool = false) throws {
        let store = try LabStore(root: root)
        let url = root.appendingPathComponent("queue.json")
        var journal = QueueJournal()
        if FileManager.default.fileExists(atPath: url.path) {
            journal = try JSONDecoder().decode(QueueJournal.self, from: Data(contentsOf: url))
        }
        var unavailable = Set<UUID>()
        let committed = try store.loadReceived { unavailable.insert($0) }
        var files: [UUID: URL] = [:]
        for row in committed {
            files[row.id] = row.fileURL
            if let index = journal.entries.firstIndex(where: { $0.id == row.id }) {
                journal.entries[index].state = .completed
                journal.entries[index].metadata = row.metadata
                journal.entries[index].bytes = row.bytes
                journal.entries[index].detail = ""
            } else {
                journal.entries.append(JournalEntry(id: row.id, direction: .incoming, mode: row.mode,
                    peer: nil, order: .distantPast, metadata: row.metadata, state: .completed, bytes: row.bytes))
            }
        }
        for index in journal.entries.indices {
            let entry = journal.entries[index]
            if entry.direction == .incoming, files[entry.id] == nil,
               entry.state == .completed || unavailable.contains(entry.id) {
                journal.entries[index].state = .interrupted
                journal.entries[index].detail = "storage_error"
            }
            if [.sending, .receiving].contains(journal.entries[index].state) {
                journal.entries[index].state = journal.entries[index].endPending ? .unconfirmed : .interrupted
                journal.entries[index].detail = "restart"
                journal.paused = true
            }
        }
        // A process restart cannot silently restart pending transfers.
        if journal.entries.contains(where: { $0.state == .queued }) { journal.paused = true }
        let retained = journal.entries.filter {
            $0.direction == .outgoing && ($0.state != .completed || $0.metadata?.kind == .text)
        }
        try store.pruneOutgoing(keeping: Set(retained.map(\.id)))
        try JSONEncoder().encode(journal).write(to: url, options: .atomic)
        self.store = store
        self.journalURL = url
        self.journal = journal
        self.receivedFiles = files
        self.secret = secret
        self.usesSystemServices = usesSystemServices
        if !unavailable.isEmpty { self.status = "storage_error" }
    }

    func persist() throws {
        do { try JSONEncoder().encode(journal).write(to: journalURL, options: .atomic) }
        catch { throw SessionFault.storage }
    }

    func mutate(_ id: UUID, _ edit: (inout JournalEntry) -> Void) {
        if let index = journal.entries.firstIndex(where: { $0.id == id }) {
            edit(&journal.entries[index])
        }
    }

    public func changes() -> AsyncStream<LabSnapshot> {
        let id = UUID()
        return AsyncStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            observers[id] = continuation
            continuation.yield(snapshot())
            continuation.onTermination = { @Sendable [weak self] _ in Task { await self?.unsubscribe(id) } }
        }
    }

    private func unsubscribe(_ id: UUID) { observers.removeValue(forKey: id) }

    func snapshot() -> LabSnapshot {
        let peer = peerID
        let rows = journal.entries.map { row in
            var url = receivedFiles[row.id]
            if row.direction == .outgoing && row.metadata?.kind == .text { url = store.outgoingURL(row.id) }
            let mismatched = row.state == .queued && row.peer != peer
            return TransferRecord(id: row.id, direction: row.direction, mode: row.mode, metadata: row.metadata,
                state: row.state, bytes: row.bytes, detail: mismatched ? "pairing_changed" : row.detail, fileURL: url)
        }
        return LabSnapshot(status: status, paired: secret != nil, paused: journal.paused,
                           connected: session?.keys != nil, records: rows)
    }

    func publish(progressOnly: Bool = false) {
        guard !observers.isEmpty else { return }
        if progressOnly {
            let now = ContinuousClock.now
            guard lastProgress.duration(to: now) >= .milliseconds(120) else { return }
            lastProgress = now
        }
        let value = snapshot()
        for observer in observers.values { observer.yield(value) }
    }

    private var peerID: Data? {
        secret.map { Data(SHA256.hash(data: Data("ULAB1/local-peer/v1".utf8) + $0)) }
    }

    private var hasWork: Bool {
        session != nil || !staging.isEmpty || journal.entries.contains { $0.state == .queued }
    }

    public func setForeground(_ active: Bool) {
        guard foreground != active else { return }
        foreground = active
        if active { startListener() }
        else {
            stopListener()
            disconnect("background", pause: hasWork)
        }
    }

    public func setPairingSecret(_ newSecret: Data?) throws {
        guard newSecret == nil || newSecret?.count == 32 else { throw SessionFault.invalidState }
        guard secret != newSecret else { return }
        if usesSystemServices { try PairingKeychain.save(newSecret) }
        stopListener()
        disconnect("pairing_changed", pause: hasWork)
        secret = newSecret
        startListener()
    }

    private func startListener() {
        guard foreground else { status = "background"; publish(); return }
        guard secret != nil else { status = "unpaired"; publish(); return }
        guard listener == nil else { publish(); return }
        if !usesSystemServices {
            status = journal.paused ? "paused" : "listening"
            publish()
            return
        }
        let id = UUID()
        listenerID = id
        status = journal.paused ? "paused" : "starting"
        do {
            listener = try LoopbackListener(onReady: { [weak self] in
                Task { await self?.listenerReady(id) }
            }, onFailure: { [weak self] in
                Task { await self?.listenerFailed(id) }
            }, onConnection: { [weak self] stream in
                Task { await self?.accept(stream, listenerID: id) }
            })
        } catch { status = "listener_failed"; listenerID = nil }
        publish()
    }

    private func stopListener() {
        listenerID = nil
        listener?.stop()
        listener = nil
    }

    private func listenerReady(_ id: UUID) {
        guard listenerID == id, session == nil else { return }
        status = journal.paused ? "paused" : "listening"
        publish()
    }

    private func listenerFailed(_ id: UUID) {
        guard listenerID == id else { return }
        stopListener()
        disconnect("listener_failed", pause: session != nil)
    }

    func accept(_ stream: SessionStream, listenerID expected: UUID? = nil) {
        guard listenerID == expected, foreground, !journal.paused,
              let secret, let peer = peerID else { stream.close(); return }
        let deadline = ContinuousClock.now.advanced(by: .seconds(10))
        if let session {
            guard session.readyEnqueued, session.incoming == nil, session.outgoing == nil,
                  pendingReconnect == nil else { stream.close(); return }
            let timer = Task { [weak self] in
                do { try await ContinuousClock().sleep(until: deadline) } catch { return }
                await self?.expirePending(stream)
            }
            pendingReconnect = (stream, deadline, timer)
            return
        }
        beginSession(stream, secret: secret, peer: peer, deadline: deadline)
    }

    private func expirePending(_ stream: SessionStream) {
        guard pendingReconnect?.stream === stream else { return }
        pendingReconnect = nil
        stream.close()
    }

    func finishDrain(_ old: LiveSession) {
        guard session === old, old.drained else { return }
        let pending = pendingReconnect
        pendingReconnect = nil
        pending?.timer.cancel()
        disconnect("listening", pause: false)
        guard let pending else { return }
        guard foreground, !journal.paused, pending.deadline > .now,
              let secret, let peer = peerID else { pending.stream.close(); return }
        beginSession(pending.stream, secret: secret, peer: peer, deadline: pending.deadline)
    }

    private func beginSession(_ stream: SessionStream, secret: Data, peer: Data, deadline: ContinuousClock.Instant) {
        let generation = LiveSession(stream: stream, peer: peer)
        generation.handshakeDeadline = deadline
        session = generation
        status = "handshake"
        publish()
        armTimeout(generation)
        Task { await run(generation, psk: secret) }
    }

    func armTimeout(_ generation: LiveSession) {
        guard session === generation else { return }
        guard let next = generation.nextDeadline else {
            generation.watchdog?.cancel()
            generation.watchdog = nil
            generation.watchdogAt = nil
            return
        }
        if let armed = generation.watchdogAt, armed <= next { return }
        generation.watchdog?.cancel()
        generation.watchdogAt = next
        generation.watchdog = Task { [weak self, weak generation] in
            do { try await ContinuousClock().sleep(until: next) } catch { return }
            guard let self, let generation else { return }
            await self.checkTimeout(generation, at: next)
        }
    }

    private func checkTimeout(_ generation: LiveSession, at deadline: ContinuousClock.Instant) {
        guard session === generation, generation.watchdogAt == deadline else { return }
        generation.watchdogAt = nil
        if generation.expired { disconnect("timeout", pause: generation.keys != nil) }
        else { armTimeout(generation) }
    }

    func validate(_ generation: LiveSession) throws {
        guard session === generation else { throw SessionFault.closed }
        guard !generation.expired else { throw SessionFault.timeout }
    }

    public func cancelActive() { disconnect("cancelled", pause: true) }

    func disconnect(_ reason: String, pause: Bool) {
        pendingReconnect?.timer.cancel()
        pendingReconnect?.stream.close()
        pendingReconnect = nil
        let old = session
        session = nil
        old?.watchdog?.cancel()
        old?.writerTail?.cancel()
        old?.stream.close()
        if let outgoing = old?.outgoing {
            mutate(outgoing.local) {
                $0.state = $0.endPending ? .unconfirmed : .interrupted
                $0.detail = reason
            }
        }
        if let incoming = old?.incoming, !incoming.committed {
            incoming.sink?.discard()
            mutate(incoming.local) { $0.state = .interrupted; $0.detail = reason }
        }
        old?.keys = nil
        if pause { journal.paused = true }
        status = reason
        do { try persist() } catch { status = "storage_error"; journal.paused = true }
        publish()
    }

    public func resume() {
        journal.paused = false
        do { try persist() } catch { journal.paused = true; status = "storage_error"; publish(); return }
        if let session { scheduleNext(session) }
        else {
            if listener == nil { startListener() }
            else { status = "listening" }
        }
        publish()
    }

    private func availablePeer() throws -> Data {
        guard let peer = peerID else { throw SessionFault.unpaired }
        let pending = journal.entries.filter { $0.state == .queued || $0.state == .sending }.count
        guard pending + staging.count < 32 else { throw SessionFault.queueFull }
        return peer
    }

    private func reserveJob() throws -> (UUID, Data, Date) {
        let peer = try availablePeer()
        let id = UUID()
        staging.insert(id)
        return (id, peer, Date())
    }

    public func enqueueText(_ text: String, mode: ProtectionMode) throws {
        let (id, peer, order) = try reserveJob()
        defer { staging.remove(id) }
        let metadata = try store.stageText(text, id: id)
        try enqueueStaged(id, metadata: metadata, mode: mode, peer: peer, order: order)
    }

    public func enqueueAttachment(_ source: URL, kind: ItemKind, name: String, mediaType: String,
                                  mode: ProtectionMode) async throws {
        let (id, peer, order) = try reserveJob()
        defer { staging.remove(id) }
        let store = self.store
        let worker = Task.detached(priority: .utility) {
            try store.stageAttachment(source, id: id, kind: kind, name: name, mediaType: mediaType)
        }
        let metadata = try await withTaskCancellationHandler { try await worker.value } onCancel: { worker.cancel() }
        if Task.isCancelled {
            try store.removeOutgoing(id)
            throw CancellationError()
        }
        try enqueueStaged(id, metadata: metadata, mode: mode, peer: peer, order: order)
    }

    private func enqueueStaged(_ id: UUID, metadata: ItemMetadata, mode: ProtectionMode, peer: Data,
                               order: Date) throws {
        journal.entries.append(JournalEntry(id: id, direction: .outgoing, mode: mode, peer: peer,
                                            order: order, metadata: metadata, state: .queued))
        do { try persist() }
        catch {
            journal.entries.removeAll { $0.id == id }
            try store.removeOutgoing(id)
            throw error
        }
        publish()
        if let session { scheduleNext(session) }
    }

    public func removeQueued(_ id: UUID) throws {
        guard let index = journal.entries.firstIndex(where: {
            $0.id == id && $0.direction == .outgoing && [.queued, .interrupted, .unconfirmed].contains($0.state)
        }) else { throw SessionFault.invalidState }
        let row = journal.entries.remove(at: index)
        do { try persist() } catch { journal.entries.insert(row, at: index); throw error }
        publish()
        try store.removeOutgoing(id)
    }

    public func retry(_ id: UUID) throws {
        guard let index = journal.entries.firstIndex(where: {
            $0.id == id && $0.direction == .outgoing && [.interrupted, .unconfirmed].contains($0.state)
        }) else { throw SessionFault.invalidState }
        let peer = try availablePeer()
        guard journal.entries[index].peer == peer else { throw SessionFault.wrongPeer }
        let previous = journal
        journal.entries[index].order = Date()
        journal.entries[index].bytes = 0
        journal.entries[index].detail = ""
        journal.entries[index].endPending = false
        journal.entries[index].state = .queued
        journal.paused = false
        do { try persist() } catch { journal = previous; throw error }
        if let session, session.keys != nil { requestDrain(session) }
        else if session != nil { disconnect("retrying", pause: false) }
        else { startListener(); if foreground && listener != nil { status = "retrying" } }
        publish()
    }

}
