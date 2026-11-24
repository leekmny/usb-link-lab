#if os(macOS)
import CryptoKit
import Foundation
import XCTest
@testable import USBLinkCore

final class CompanionInteropTests: XCTestCase {
    func testWindowsPythonSessionTransfersAllModesThroughSwiftTCPServer() async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let source = environment["ULAB_WINDOWS_SOURCE"], !source.isEmpty else {
            throw XCTSkip("Set ULAB_WINDOWS_SOURCE to the Windows companion apps/windows directory")
        }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }

        let secret = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
        let serverRoot = root.appendingPathComponent("swift-store", isDirectory: true)
        let server = try USBServer(root: serverRoot, secret: secret, usesSystemServices: true)
        let fixtures = CompanionFixtures(
            fromSwift: try makeSamples(sender: "swift", root: root),
            fromPython: try makeSamples(sender: "python", root: root)
        )
        try JSONEncoder().encode(fixtures).write(to: root.appendingPathComponent("fixtures.json"), options: .atomic)
        for sample in fixtures.fromSwift {
            let url = root.appendingPathComponent(sample.file)
            if sample.kind == .text {
                try await server.enqueueText(String(contentsOf: url, encoding: .utf8), mode: sample.mode)
            } else {
                try await server.enqueueAttachment(
                    url, kind: sample.kind, name: sample.name, mediaType: sample.mediaType, mode: sample.mode
                )
            }
        }

        await server.setForeground(true)
        do {
            try await waitForListener(server)
            try await runPython(root: root, source: source, secret: secret, environment: environment)
            let snapshot = await server.snapshot()
            let outgoing = snapshot.records.filter { $0.direction == .outgoing }
            let incoming = snapshot.records.filter { $0.direction == .incoming }
            try verifyRows(outgoing, samples: fixtures.fromSwift, root: root, checkContents: false)
            try verifyRows(incoming, samples: fixtures.fromPython, root: root, checkContents: true)
            let persisted = try LabStore(root: serverRoot).loadReceived()
            try verifyRows(persisted, samples: fixtures.fromPython, root: root, checkContents: true)
        } catch {
            await server.setForeground(false)
            throw error
        }
        await server.setForeground(false)
    }

    private func waitForListener(_ server: USBServer) async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline {
            let status = await server.snapshot().status
            if status == "listening" { return }
            if status == "listener_failed" { throw CompanionFailure("Swift loopback listener failed") }
            try await Task.sleep(for: .milliseconds(25))
        }
        let status = await server.snapshot().status
        throw CompanionFailure("Swift loopback listener did not become ready: \(status)")
    }

    private func runPython(root: URL, source: String, secret: Data, environment: [String: String]) async throws {
        let script = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("scripts/test-windows-companion.py")
        var childEnvironment = environment
        childEnvironment["ULAB_WINDOWS_SOURCE"] = URL(fileURLWithPath: source, isDirectory: true).standardizedFileURL.path
        childEnvironment["ULAB_TEST_PSK"] = secret.map { String(format: "%02x", $0) }.joined()
        childEnvironment["PYTHONDONTWRITEBYTECODE"] = "1"

        let process = Process()
        process.environment = childEnvironment
        process.currentDirectoryURL = root
        if let python = environment["ULAB_PYTHON"], !python.isEmpty {
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = [script.path, root.path]
        } else {
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = ["python3", script.path, root.path]
        }

        let log = root.appendingPathComponent("python.log")
        try Data().write(to: log)
        let output = try FileHandle(forWritingTo: log)
        defer { try? output.close() }
        process.standardOutput = output
        process.standardError = output
        try process.run()
        defer {
            if process.isRunning { process.terminate() }
            process.waitUntilExit()
        }

        let deadline = ContinuousClock.now.advanced(by: .seconds(40))
        while process.isRunning, ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(25))
        }
        guard !process.isRunning else { throw CompanionFailure("Windows Python helper timed out") }
        let diagnostics = try String(contentsOf: log, encoding: .utf8)
        guard process.terminationStatus == 0 else {
            throw CompanionFailure("Windows Python helper exited \(process.terminationStatus)\n\(diagnostics)")
        }
        XCTAssertTrue(diagnostics.contains("Windows companion verified 12 outgoing ACKs and 12 persisted incoming items"))
        print(diagnostics.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private func makeSamples(sender: String, root: URL) throws -> [CompanionSample] {
        // A synthetic 1x1 PNG, plus a binary and a UTF-8 scalar split at a DATA boundary.
        let image = sender == "swift"
            ? "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGPgmvYBAAI+AZHTTKrzAAAAAElFTkSuQmCC"
            : "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4EMUPAAOXAVrHjReFAAAAAElFTkSuQmCC"
        let png = try XCTUnwrap(Data(base64Encoded: image))
        var samples: [CompanionSample] = []
        for mode in ProtectionMode.allCases {
            let slug = "\(sender)-\(mode.rawValue)"
            let text = String(repeating: sender == "swift" ? "S" : "P", count: 65_535) + "안녕 \(slug)\n"
            var binary = Data(repeating: sender == "swift" ? 0x53 : 0x50, count: 65_537)
            binary[0] = 0
            binary[binary.count - 1] = mode.rawValue
            let contents: [(ItemKind, String, String, Data)] = [
                (.text, "", "text/plain; charset=utf-8", Data(text.utf8)),
                (.file, "\(slug).bin", "application/octet-stream", binary),
                (.image, "\(slug).png", "image/png", png)
            ]
            for (kind, name, mediaType, data) in contents {
                let file = "\(slug)-\(kind.rawValue).fixture"
                try data.write(to: root.appendingPathComponent(file))
                samples.append(CompanionSample(mode: mode, kind: kind, name: name, mediaType: mediaType, file: file))
            }
        }
        return samples
    }

    private func verifyRows(
        _ rows: [TransferRecord], samples: [CompanionSample], root: URL, checkContents: Bool
    ) throws {
        XCTAssertEqual(rows.count, samples.count)
        for sample in samples {
            let label = "\(sample.mode) \(sample.kind)"
            let matches = rows.filter { $0.mode == sample.mode && $0.metadata?.kind == sample.kind }
            XCTAssertEqual(matches.count, 1, label)
            let row = try XCTUnwrap(matches.first, label)
            let metadata = try XCTUnwrap(row.metadata, label)
            let expected = try Data(contentsOf: root.appendingPathComponent(sample.file))
            XCTAssertEqual(row.state, .completed, "\(label) \(row.detail)")
            XCTAssertEqual(row.bytes, UInt64(expected.count), label)
            XCTAssertEqual(metadata.name, sample.name, label)
            XCTAssertEqual(metadata.mediaType, sample.mediaType, label)
            XCTAssertEqual(metadata.size, UInt64(expected.count), label)
            if checkContents {
                let stored = try XCTUnwrap(row.fileURL, label)
                XCTAssertEqual(try Data(contentsOf: stored), expected, label)
            }
        }
    }
}

private struct CompanionFixtures: Encodable {
    let fromSwift: [CompanionSample]
    let fromPython: [CompanionSample]
}

private struct CompanionSample: Encodable {
    let mode: ProtectionMode
    let kind: ItemKind
    let name: String
    let mediaType: String
    let file: String
}

private struct CompanionFailure: Error, CustomStringConvertible {
    let description: String

    init(_ description: String) {
        self.description = description
    }
}
#endif
