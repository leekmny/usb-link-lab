import Foundation
import Network

/// One ordered byte stream. A short read is normal. Only one reader is permitted.
protocol SessionStream: AnyObject, Sendable {
    func read(upTo count: Int) async throws -> Data
    func write(_ bytes: Data) async throws
    func close()
}

enum SessionFault: Error, LocalizedError, Equatable {
    case closed, protocolViolation, timeout, storage, unpaired, queueFull, wrongPeer, invalidState

    var errorDescription: String? {
        switch self {
        case .closed: return "connection_closed"
        case .protocolViolation: return "protocol_error"
        case .timeout: return "timeout"
        case .storage: return "storage_error"
        case .unpaired: return "unpaired"
        case .queueFull: return "queue_full"
        case .wrongPeer: return "pairing_changed"
        case .invalidState: return "invalid_state"
        }
    }
}

extension SessionStream {
    func exact(_ count: Int) async throws -> Data {
        var result = Data()
        result.reserveCapacity(count)
        while result.count < count {
            let part = try await read(upTo: count - result.count)
            guard !part.isEmpty, part.count <= count - result.count else { throw SessionFault.closed }
            result.append(part)
        }
        return result
    }
}

final class LoopbackStream: SessionStream, @unchecked Sendable {
    private let connection: NWConnection
    private let queue = DispatchQueue(label: "USBLinkLab.loopback.stream")
    // Network completion handlers and the following flag are confined to queue.
    private var eof = false

    init(_ connection: NWConnection) {
        self.connection = connection
        connection.start(queue: queue)
    }

    func read(upTo count: Int) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            queue.async {
                guard !self.eof else {
                    continuation.resume(throwing: SessionFault.closed)
                    return
                }
                self.connection.receive(minimumIncompleteLength: 1, maximumLength: count) { bytes, _, last, error in
                    if last || error != nil { self.eof = true }
                    if let bytes, !bytes.isEmpty {
                        continuation.resume(returning: bytes)
                    } else {
                        continuation.resume(throwing: SessionFault.closed)
                    }
                }
            }
        }
    }

    func write(_ bytes: Data) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            connection.send(content: bytes, completion: .contentProcessed { error in
                if error != nil { continuation.resume(throwing: SessionFault.closed) }
                else { continuation.resume() }
            })
        }
    }

    func close() { connection.cancel() }
}

final class LoopbackListener {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "USBLinkLab.loopback.listener")

    init(onReady: @escaping @Sendable () -> Void, onFailure: @escaping @Sendable () -> Void,
         onConnection: @escaping @Sendable (SessionStream) -> Void) throws {
        let port: NWEndpoint.Port = 43123
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: port)
        listener = try NWListener(using: parameters)
        listener.newConnectionHandler = { connection in
            // Never broaden the endpoint if device forwarding is unavailable.
            guard case let .hostPort(.ipv4(address), _) = connection.endpoint,
                  address.rawValue.first == 127 else {
                connection.cancel()
                return
            }
            onConnection(LoopbackStream(connection))
        }
        listener.stateUpdateHandler = { state in
            switch state {
            case .ready: onReady()
            case .failed: onFailure()
            default: break
            }
        }
        listener.start(queue: queue)
    }

    func stop() { listener.cancel() }
}
