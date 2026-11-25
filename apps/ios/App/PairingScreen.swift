import Foundation
import SwiftUI
import USBLinkCore

struct PairingScreen: View {
    let language: LabLanguage

    @EnvironmentObject private var model: LabModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @FocusState private var codeFocused: Bool
    @State private var enteringCode = false
    @State private var code = ""
    @State private var scannedInvitation: PairingInvitation?
    @State private var scanning = false
    @State private var cameraRequest: Task<Void, Never>?
    @State private var cameraFeedback: LabCopy?
    @State private var preparation: Task<Void, Never>?
    @State private var prepareFailed = false

    private var pairingInProgress: Bool {
        preparation != nil || model.connection.pairingStatus == .ready
            || model.connection.pairingStatus == .connecting
    }

    private var codeInvitation: PairingInvitation? { try? .sixDigitCode(code) }

    var body: some View {
        NavigationStack {
            Group {
                if scanning && scenePhase == .active {
                    scanner
                } else {
                    LabScroll { pairingContent }
                }
            }
            .navigationTitle(language.text(scanning ? .scanWindowsQR : .pairing))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    if scanning || enteringCode || scannedInvitation != nil {
                        Button(language.text(.back)) { clearDraft() }
                    } else {
                        Button(language.text(model.connection.pairingStatus == .paired ? .done : .dismiss)) {
                            if pairingInProgress {
                                clearDraft()
                                Task {
                                    await model.cancelPairing()
                                    dismiss()
                                }
                            } else {
                                dismiss()
                            }
                        }
                    }
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                if model.synthetic { LabSyntheticBanner(language: language) }
            }
        }
        .modifier(LabPrivacyCover())
        .interactiveDismissDisabled(pairingInProgress)
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { clearDraft(cancelCameraRequest: phase == .background) }
        }
        .onDisappear {
            clearDraft()
            Task { await model.cancelPairing() }
        }
    }

    @ViewBuilder
    private var pairingContent: some View {
        switch model.connection.pairingStatus {
        case .ready:
            VStack(alignment: .leading, spacing: 10) {
                statusTitle(.pairingReady, symbol: "checkmark.circle", identifier: "pairing-ready")
                instructions(.startWindowsPairing)
                if let expiry = model.connection.pairingExpiresAt {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let remaining = max(0, Int(ceil(expiry.timeIntervalSince(context.date))))
                        Text(language.format(.pairingTimeRemaining, remaining))
                            .font(.subheadline.monospacedDigit().weight(.semibold))
                            .foregroundStyle(LabPalette.teal)
                            .accessibilityIdentifier("pairing-countdown")
                    }
                }
            }
            .modifier(LabPanel())
            cancelAttemptButton
        case .connecting:
            progress(.pairingConnecting)
            cancelAttemptButton
        case .paired:
            statusPanel(.pairingCompleted, help: .pairingCompletedHelp, symbol: "checkmark.shield")
        case .idle, .expired, .failed:
            if preparation != nil {
                progress(.preparingPairing)
            } else if enteringCode {
                codeEntry
            } else if scannedInvitation != nil {
                VStack(alignment: .leading, spacing: 10) {
                    statusTitle(.qrRecognized, symbol: "checkmark.circle")
                    prepareButton(enabled: true) { scannedInvitation }
                }
                .modifier(LabPanel())
            } else {
                invitationOptions
            }
        }
    }

    private var invitationOptions: some View {
        VStack(alignment: .leading, spacing: 12) {
            switch model.connection.pairingStatus {
            case .expired:
                statusPanel(.pairingExpired, help: .pairingExpiredHelp, symbol: "clock.badge.exclamationmark", caution: true)
            case .failed:
                statusPanel(.pairingAttemptFailed, help: .pairingTryAgain, symbol: "exclamationmark.circle", caution: true)
            default:
                if prepareFailed {
                    statusPanel(.pairingAttemptFailed, help: .pairingTryAgain, symbol: "exclamationmark.circle", caution: true)
                } else {
                    instructions(.pairingExplanation)
                }
            }

            Button(action: requestScanner) {
                Label(language.text(.scanWindowsQR), systemImage: "qrcode.viewfinder")
            }
            .buttonStyle(LabPrimaryButton())
            .disabled(model.busy || cameraRequest != nil)
            .accessibilityIdentifier("scan-pairing-qr")

            if let cameraFeedback {
                Text(language.text(cameraFeedback))
                    .font(.subheadline)
                    .foregroundStyle(LabPalette.caution)
                    .accessibilityIdentifier("pairing-camera-feedback")
            }

            Button {
                clearDraft()
                enteringCode = true
            } label: {
                Label(language.text(.enterPairingCode), systemImage: "number")
                    .font(.body.weight(.semibold))
                    .frame(minHeight: 44)
                    .frame(maxWidth: .infinity)
            }
            .disabled(model.busy || cameraRequest != nil)
            .accessibilityIdentifier("enter-pairing-code")
        }
    }

    private var codeEntry: some View {
        let codeValid = codeInvitation != nil
        return VStack(alignment: .leading, spacing: 10) {
            instructions(.enterWindowsCode)
            SecureField(language.text(.pairingCodeField), text: $code)
                .focused($codeFocused)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .keyboardType(.asciiCapableNumberPad)
                .font(.body.monospaced())
                .padding(.horizontal, 11)
                .frame(minHeight: 44)
                .background(LabPalette.inset, in: RoundedRectangle(cornerRadius: 10))
                .privacySensitive()
                .accessibilityIdentifier("pairing-code")
            if !code.isEmpty && !codeValid {
                Text(language.text(.invalidPairingCode))
                    .font(.footnote)
                    .foregroundStyle(LabPalette.caution)
            }
            prepareButton(enabled: codeValid) { codeInvitation }
        }
        .modifier(LabPanel())
    }

    private var scanner: some View {
        VStack(alignment: .leading, spacing: 10) {
            instructions(.showWindowsQR)
                .padding(.horizontal, 16)
            PairingQRScanner { result in
                guard scanning, scenePhase == .active else { return }
                scanning = false
                switch result {
                case let .invitation(invitation):
                    scannedInvitation = invitation
                case .invalid:
                    cameraFeedback = .invalidPairingQR
                case .unavailable:
                    cameraFeedback = .cameraUnavailable
                }
            }
            .privacySensitive()
        }
        .padding(.top, 12)
        .background(LabPalette.canvas)
    }

    private func instructions(_ copy: LabCopy) -> some View {
        Text(language.text(copy))
            .font(.subheadline)
            .foregroundStyle(LabPalette.muted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func statusTitle(
        _ copy: LabCopy, symbol: String, caution: Bool = false, identifier: String = "pairing-status"
    ) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: symbol)
                .accessibilityHidden(true)
            Text(language.text(copy))
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier(identifier)
        }
        .font(.headline)
        .foregroundStyle(caution ? LabPalette.caution : LabPalette.teal)
    }

    private func statusPanel(_ copy: LabCopy, help: LabCopy, symbol: String, caution: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            statusTitle(copy, symbol: symbol, caution: caution)
            instructions(help)
        }
        .modifier(LabPanel())
    }

    private func progress(_ copy: LabCopy) -> some View {
        ProgressView(language.text(copy))
            .font(.body)
            .modifier(LabPanel())
    }

    private var cancelAttemptButton: some View {
        Button {
            Task { await model.cancelPairing() }
        } label: {
            Text(language.text(.cancelPairing))
                .frame(minHeight: 44)
                .frame(maxWidth: .infinity)
        }
        .accessibilityIdentifier("cancel-pairing")
    }

    private func prepareButton(enabled: Bool, invitation: @escaping () -> PairingInvitation?) -> some View {
        Button {
            guard let invitation = invitation(), preparation == nil else { return }
            clearDraft()
            preparation = Task {
                let prepared = await model.preparePairing(invitation)
                guard !Task.isCancelled else { return }
                prepareFailed = !prepared
                preparation = nil
            }
        } label: {
            Text(language.text(.preparePairing))
        }
        .buttonStyle(LabPrimaryButton())
        .disabled(!enabled || model.busy)
        .accessibilityIdentifier("prepare-pairing")
    }

    private func requestScanner() {
        clearDraft()
        cameraRequest = Task {
            let feedback: LabCopy?
            if model.synthetic {
                feedback = .cameraUnavailable
            } else {
                feedback = await PairingQRScanner.requestAccess()
            }
            guard !Task.isCancelled else { return }
            cameraRequest = nil
            cameraFeedback = feedback
            scanning = feedback == nil
        }
    }

    private func clearDraft(cancelCameraRequest: Bool = true) {
        codeFocused = false
        code = ""
        enteringCode = false
        scannedInvitation = nil
        scanning = false
        cameraFeedback = nil
        prepareFailed = false
        preparation?.cancel()
        preparation = nil
        if cancelCameraRequest {
            cameraRequest?.cancel()
            cameraRequest = nil
        }
    }
}
