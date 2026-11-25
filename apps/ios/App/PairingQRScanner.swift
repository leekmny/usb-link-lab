import AVFoundation
import SwiftUI
import UIKit
import USBLinkCore
import VisionKit

struct PairingQRScanner: UIViewControllerRepresentable {
    enum Result {
        case invitation(PairingInvitation)
        case invalid
        case unavailable
    }

    let onResult: (Result) -> Void

    @MainActor
    static func requestAccess() async -> LabCopy? {
        guard DataScannerViewController.isSupported else { return .cameraUnavailable }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            break
        case .notDetermined:
            guard await AVCaptureDevice.requestAccess(for: .video) else { return .cameraDenied }
        case .denied, .restricted:
            return .cameraDenied
        @unknown default:
            return .cameraUnavailable
        }
        return DataScannerViewController.isAvailable ? nil : .cameraUnavailable
    }

    func makeCoordinator() -> Coordinator { Coordinator(onResult: onResult) }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(
            recognizedDataTypes: [.barcode(symbologies: [.qr])],
            qualityLevel: .balanced,
            recognizesMultipleItems: false,
            isHighFrameRateTrackingEnabled: false,
            isPinchToZoomEnabled: true,
            isGuidanceEnabled: false,
            isHighlightingEnabled: false
        )
        let coordinator = context.coordinator
        scanner.delegate = coordinator
        Task { @MainActor [weak scanner, weak coordinator] in
            guard let scanner, let coordinator else { return }
            coordinator.start(scanner)
        }
        return scanner
    }

    func updateUIViewController(_ scanner: DataScannerViewController, context: Context) {
        context.coordinator.onResult = onResult
    }

    static func dismantleUIViewController(_ scanner: DataScannerViewController, coordinator: Coordinator) {
        coordinator.stop(scanner)
    }

    @MainActor
    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        var onResult: (Result) -> Void
        private var finished = false

        init(onResult: @escaping (Result) -> Void) { self.onResult = onResult }

        func start(_ scanner: DataScannerViewController) {
            guard !finished else { return }
            do {
                try scanner.startScanning()
            } catch {
                finish(.unavailable, in: scanner)
            }
        }

        func stop(_ scanner: DataScannerViewController) {
            finished = true
            scanner.delegate = nil
            scanner.stopScanning()
        }

        func dataScanner(
            _ scanner: DataScannerViewController, didAdd addedItems: [RecognizedItem], allItems: [RecognizedItem]
        ) {
            guard !finished, case let .barcode(barcode)? = addedItems.first else { return }
            guard let payload = barcode.payloadStringValue,
                  let invitation = try? PairingInvitation.scanQR(payload) else {
                finish(.invalid, in: scanner)
                return
            }
            finish(.invitation(invitation), in: scanner)
        }

        func dataScanner(
            _ scanner: DataScannerViewController, becameUnavailableWithError error: DataScannerViewController.ScanningUnavailable
        ) {
            finish(.unavailable, in: scanner)
        }

        private func finish(_ result: Result, in scanner: DataScannerViewController) {
            guard !finished else { return }
            stop(scanner)
            onResult(result)
        }
    }
}
