#if os(macOS)
import Foundation
import Network
import XCTest
@testable import USBLinkCore

final class SessionLoopbackTests: XCTestCase {
    func testPublishedListenerBindsOnlyIPv4LoopbackAndCarriesBytesBothWays() async throws {
        let listening = expectation(description: "Listener ready")
        let accepted = expectation(description: "Accepted byte stream")
        let response = expectation(description: "Round trip")
        let payload = Data("local TCP simulation".utf8)
        let listener = try LoopbackListener(onReady: { listening.fulfill() }, onFailure: {
            XCTFail("Loopback listener failed to start")
        }, onConnection: { stream in
            Task {
                defer { stream.close(); accepted.fulfill() }
                do {
                    let actual = try await stream.exact(payload.count)
                    XCTAssertEqual(actual, payload)
                    try await stream.write(actual)
                } catch { XCTFail("Server-side loopback exchange failed: \(error)") }
            }
        })
        defer { listener.stop() }
        await fulfillment(of: [listening], timeout: 5)

        let output = Pipe()
        let lsof = Process()
        lsof.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        lsof.arguments = ["-nP", "-a", "-p", String(ProcessInfo.processInfo.processIdentifier),
                          "-iTCP:43123", "-sTCP:LISTEN", "-Fn"]
        lsof.standardOutput = output
        try lsof.run()
        let description = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        lsof.waitUntilExit()
        XCTAssertEqual(lsof.terminationStatus, 0, description)
        XCTAssertEqual(description.split(separator: "\n").filter { $0.hasPrefix("n") },
                       ["n127.0.0.1:43123"], description)

        let connection = NWConnection(host: .ipv4(.loopback), port: 43123, using: .tcp)
        let client = LoopbackStream(connection)
        defer { client.close() }
        Task {
            defer { response.fulfill() }
            do {
                try await client.write(payload)
                let echo = try await client.exact(payload.count)
                XCTAssertEqual(echo, payload)
            } catch { XCTFail("Client-side loopback exchange failed: \(error)") }
        }
        await fulfillment(of: [accepted, response], timeout: 5)
    }
}
#endif
