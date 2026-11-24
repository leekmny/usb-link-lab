import Foundation
import CryptoKit

extension USBServer {
    func run(_ s: LiveSession, accepted: ContinuousClock.Instant) async {
        do {
            try await authenticate(s, accepted: accepted)
            try validate(s)
            status = "connected"
            publish()
            scheduleNext(s)
            while true {
                // Idle connections may wait indefinitely for the first byte.
                var prefix = try await s.stream.read(upTo: 1)
                try validate(s)
                if prefix.isEmpty {
                    // The peer can observe our READY and close before its local write
                    // callback resumes. Confirm that write at this clean frame boundary.
                    guard s.readyReceived, s.readyEnqueued else { throw SessionFault.closed }
                    if !s.readyWritten {
                        guard let ticket = s.writerTail else { throw SessionFault.closed }
                        try await ticket.value
                        try validate(s)
                        s.readyWritten = true
                    }
                    finishDrain(s)
                    return
                }
                guard prefix.count == 1 else { throw SessionFault.protocolViolation }
                s.frameDeadline = .now.advanced(by: .seconds(10))
                armTimeout(s)
                prefix.append(try await s.stream.exact(3))
                _ = try Wire.prefixLength(prefix)
                let prefixAndHeader = prefix + (try await s.stream.exact(72))
                try validate(s)
                guard let keys = s.keys else { throw SessionFault.closed }
                let header = try Wire.decodeHeader(prefixAndHeader, keys: keys.c2s,
                                                   expectedSequence: s.receiveBudget.frames)
                try s.receiveBudget.charge(header.length)
                let wire = try await s.stream.exact(Wire.wireBodyLength(header))
                try validate(s)
                let body = try Wire.decodeBody(wire, prefixAndHeaderAndMAC: prefixAndHeader,
                                               header: header, keys: keys.c2s)
                s.frameDeadline = nil
                try await receive(header, body: body, session: s)
                armTimeout(s)
            }
        } catch { fail(error, session: s) }
    }

    func fail(_ error: Error, session s: LiveSession) {
        guard session === s else { return }
        let reason = (error as? SessionFault)?.errorDescription ?? "protocol_error"
        disconnect(reason, pause: s.keys != nil)
    }

    // A single actor reserves sequences and commits even queued bytes immediately.
    // Each task waits for its predecessor's complete write. The reader and the two
    // item producers can issue bounded work without interleaving TCP record bytes.
    func submit(_ type: FrameType, mode: ProtectionMode, id: Data, index: UInt64 = 0,
                body: Data, session s: LiveSession, starting: (() -> Void)? = nil,
                written: (() -> Void)? = nil) throws -> Task<Void, Error> {
        try validate(s)
        guard let keys = s.keys else { throw SessionFault.closed }
        let sequence = s.sendBudget.frames
        let header = FrameHeader(type: type, mode: mode, sequence: sequence, itemID: id,
                                 index: index, length: body.count)
        let packet = try Wire.encode(header, body: body, keys: keys.s2c)
        try s.sendBudget.charge(body.count)
        let prior = s.writerTail
        let task = Task {
            if let prior { try await prior.value }
            try validate(s)
            starting?()
            try await s.stream.write(packet)
            try validate(s)
            written?()
        }
        s.writerTail = task
        return task
    }

    func scheduleNext(_ s: LiveSession) {
        guard session === s, s.keys != nil, !s.requestEnqueued, !journal.paused,
              s.outgoing == nil else { return }
        guard let job = journal.entries.filter({
            $0.direction == .outgoing && $0.state == .queued && $0.peer == s.peer
        }).min(by: { $0.order < $1.order }), let meta = job.metadata else { return }
        guard s.sendBudget.canAdmit(size: meta.size, localIDs: s.localIDs.count,
                                    peerCompletions: s.peerCompletions) else { requestDrain(s); return }
        let zero = Data(repeating: 0, count: 16)
        var id = randomBytes(16)
        while id == zero || s.localIDs.contains(id) { id = randomBytes(16) }
        s.localIDs.insert(id)
        let item = SendingItem(local: job.id, wire: id, mode: job.mode, metadata: meta)
        s.outgoing = item
        armTimeout(s)
        mutate(job.id) { $0.state = .sending; $0.bytes = 0 }
        do {
            try persist()
            let ticket = try submit(.open, mode: item.mode, id: id, body: WireBody.open(item.mode), session: s,
                                    starting: { item.waitingFrameDispatched = true }, written: {
                if s.outgoing === item, item.stage == .waitingReady {
                    item.deadline = .now.advanced(by: .seconds(60))
                }
            })
            Task {
                do { try await ticket.value } catch { fail(error, session: s) }
            }
            if s.localIDs.count == SessionBudget.idLimit { requestDrain(s) }
            publish()
        } catch { fail(error, session: s) }
    }

    private func receive(_ h: FrameHeader, body: Data, session s: LiveSession) async throws {
        switch h.type {
        case .open:
            guard !journal.paused else { throw SessionFault.closed }
            try WireBody.decodeOpen(body, mode: h.mode)
            guard !s.requestReceived, s.incoming == nil, s.peerIDs.count < SessionBudget.idLimit,
                  s.peerIDs.insert(h.itemID).inserted else { throw SessionFault.protocolViolation }
            let item = ReceivingItem(wire: h.itemID, mode: h.mode)
            s.incoming = item
            s.drainDeadline = nil
            journal.entries.append(JournalEntry(id: item.local, direction: .incoming, mode: h.mode, peer: s.peer,
                                                order: Date(), metadata: nil, state: .receiving))
            try persist()
            publish()
            try await submit(.ack, mode: h.mode, id: h.itemID, body: WireBody.ack(.ready), session: s).value
            try validate(s)
            item.deadline = .now.advanced(by: .seconds(60))
        case .meta, .data, .end:
            guard let item = s.incoming, item.wire == h.itemID, item.mode == h.mode,
                  !item.committed else { throw SessionFault.protocolViolation }
            switch h.type {
            case .meta:
                guard item.metadata == nil else { throw SessionFault.protocolViolation }
                let meta = try ItemMetadata.decode(body)
                item.sink = try disk { try store.beginIncoming(item.local, metadata: meta) }
                item.metadata = meta
                mutate(item.local) { $0.metadata = meta }
                try persist()
                publish()
            case .data:
                guard let meta = item.metadata, let sink = item.sink, h.index == item.chunks,
                      item.bytes < meta.size, UInt64(body.count) == min(meta.size - item.bytes, 65536)
                else { throw SessionFault.protocolViolation }
                try disk { try sink.append(body) }
                item.hash.update(data: body)
                item.bytes += UInt64(body.count)
                item.chunks += 1
                mutate(item.local) { $0.bytes = item.bytes }
                publish(progressOnly: true)
            default:
                try await finishIncoming(h, body: body, item: item, session: s)
            }
            item.deadline = .now.advanced(by: .seconds(60))
        case .ack:
            guard let item = s.outgoing, item.wire == h.itemID, item.mode == h.mode,
                  item.waitingFrameDispatched else {
                throw SessionFault.protocolViolation
            }
            switch try WireBody.decodeAck(body) {
            case .ready:
                guard item.stage == .waitingReady else { throw SessionFault.protocolViolation }
                item.stage = .sending
                item.waitingFrameDispatched = false
                item.deadline = .now.advanced(by: .seconds(60))
                Task { await sendContent(item, session: s) }
            case .complete:
                guard item.stage == .waitingComplete else { throw SessionFault.protocolViolation }
                mutate(item.local) { $0.state = .completed; $0.endPending = false; $0.bytes = item.metadata.size }
                s.outgoing = nil
                try persist()
                if item.metadata.kind != .text { try disk { try store.removeOutgoing(item.local) } }
                publish()
                advanceDrain(s)
                scheduleNext(s)
            }
        case .drain:
            switch try WireBody.decodeDrain(body) {
            case .request:
                guard !s.requestReceived else { throw SessionFault.protocolViolation }
                s.requestReceived = true
                requestDrain(s)
                advanceDrain(s)
            case .ready:
                guard s.requestReceived, s.requestDispatched, !s.readyReceived,
                      s.incoming == nil, s.outgoing == nil else { throw SessionFault.protocolViolation }
                s.readyReceived = true
                advanceDrain(s)
            }
        case .error:
            try WireBody.decodeError(body)
            throw SessionFault.protocolViolation
        }
    }

    private func finishIncoming(_ h: FrameHeader, body: Data, item: ReceivingItem,
                                session s: LiveSession) async throws {
        let end = try WireBody.decodeEnd(body)
        guard let meta = item.metadata, let sink = item.sink, h.index == item.chunks,
              end.chunks == item.chunks, item.bytes == meta.size, end.bytes == item.bytes,
              end.sha256 == digestString(item.hash.finalize()) else { throw SessionFault.protocolViolation }
        let file = try disk { try sink.commit(mode: item.mode) }
        item.committed = true
        item.sink = nil
        receivedFiles[item.local] = file
        mutate(item.local) { $0.state = .completed; $0.bytes = item.bytes }
        try persist()
        publish()
        try await submit(.ack, mode: item.mode, id: item.wire, body: WireBody.ack(.complete), session: s).value
        try validate(s)
        s.peerCompletions += 1
        s.incoming = nil
        advanceDrain(s)
    }

    private func sendContent(_ item: SendingItem, session s: LiveSession) async {
        do {
            try validate(s)
            guard s.outgoing === item else { throw SessionFault.closed }
            let source = try disk { try FileHandle(forReadingFrom: store.outgoingURL(item.local)) }
            defer { try? source.close() }
            let meta = try item.metadata.encode()
            try await submit(.meta, mode: item.mode, id: item.wire, body: meta, session: s).value
            try validate(s)
            item.deadline = .now.advanced(by: .seconds(60))
            var count: UInt64 = 0
            var index: UInt64 = 0
            var hash = SHA256()
            while count < item.metadata.size {
                let wanted = Int(min(item.metadata.size - count, 65536))
                var chunk = Data()
                while chunk.count < wanted {
                    let part = try disk { try source.read(upToCount: wanted - chunk.count) }
                    guard let part, !part.isEmpty else { throw SessionFault.storage }
                    chunk.append(part)
                }
                hash.update(data: chunk)
                try await submit(.data, mode: item.mode, id: item.wire, index: index,
                                 body: chunk, session: s).value
                try validate(s)
                count += UInt64(chunk.count)
                index += 1
                item.deadline = .now.advanced(by: .seconds(60))
                mutate(item.local) { $0.bytes = count }
                publish(progressOnly: true)
            }
            let end = try WireBody.end(bytes: count, chunks: index, sha256: digestString(hash.finalize()))
            item.stage = .waitingComplete
            mutate(item.local) { $0.endPending = true }
            try persist()
            let ticket = try submit(.end, mode: item.mode, id: item.wire, index: index, body: end, session: s,
                                    starting: { item.waitingFrameDispatched = true }, written: {
                if s.outgoing === item { item.deadline = .now.advanced(by: .seconds(60)) }
            })
            try await ticket.value
        } catch { fail(error, session: s) }
    }

    func requestDrain(_ s: LiveSession) {
        guard session === s, !s.requestEnqueued else { return }
        s.requestEnqueued = true
        status = "draining"
        do {
            let ticket = try submit(.drain, mode: .plaintext, id: Data(repeating: 0, count: 16),
                                    body: WireBody.drain(.request), session: s,
                                    starting: { s.requestDispatched = true })
            Task {
                do {
                    try await ticket.value
                    try validate(s)
                    s.requestWritten = true
                    advanceDrain(s)
                } catch { fail(error, session: s) }
            }
            advanceDrain(s)
            publish()
        } catch { fail(error, session: s) }
    }

    private func advanceDrain(_ s: LiveSession) {
        guard s.requestEnqueued, s.incoming == nil, s.outgoing == nil else { return }
        if s.drainDeadline == nil { s.drainDeadline = .now.advanced(by: .seconds(60)) }
        armTimeout(s)
        guard s.requestWritten, s.requestReceived, !s.readyEnqueued else { return }
        s.readyEnqueued = true
        do {
            let ticket = try submit(.drain, mode: .plaintext, id: Data(repeating: 0, count: 16),
                                    body: WireBody.drain(.ready), session: s)
            Task {
                do {
                    try await ticket.value
                    try validate(s)
                    s.readyWritten = true
                } catch { fail(error, session: s) }
            }
        } catch { fail(error, session: s) }
    }

    private func disk<T>(_ action: () throws -> T) throws -> T {
        do { return try action() }
        catch StoreFailure.invalidText { throw SessionFault.protocolViolation }
        catch { throw SessionFault.storage }
    }
}
