import Foundation
import SwiftUI
import UIKit
import UniformTypeIdentifiers
import USBLinkCore

struct SettingsScreen: View {
    let language: LabLanguage
    @Binding var selectedLanguage: LabLanguage
    @Binding var appearance: LabAppearance

    @EnvironmentObject private var model: LabModel
    @State private var pairing = false
    @State private var forgetting = false
    @State private var diagnostics = false

    var body: some View {
        LabScroll {
            LabSection(title: language.text(.pairing)) {
                VStack(alignment: .leading, spacing: 12) {
                    LabFact(label: language.text(.status), value: language.text(model.connection.paired ? .pairedState : .unpaired))
                    Button { pairing = true } label: {
                        Label(language.text(.setupLink), systemImage: "key.horizontal")
                    }
                    .buttonStyle(LabPrimaryButton())
                    .disabled(!model.ready || model.busy)
                    if model.connection.paired {
                        Button(role: .destructive) { forgetting = true } label: {
                            Label(language.text(.forgetPairing), systemImage: "trash")
                                .frame(minHeight: 44)
                        }
                        .foregroundStyle(LabPalette.caution)
                        .disabled(model.busy)
                    }
                }
                .modifier(LabPanel())
            }

            LabSection(title: language.text(.appearance)) {
                VStack(spacing: 5) {
                    Picker(selection: $selectedLanguage) {
                        ForEach(LabLanguage.allCases) { choice in Text(choice.name).tag(choice) }
                    } label: {
                        Label(language.text(.language), systemImage: "character.bubble")
                    }
                    .pickerStyle(.menu)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .disabled(model.synthetic)
                    Divider()
                    Picker(selection: $appearance) {
                        ForEach(LabAppearance.allCases) { choice in Text(language.text(choice.word)).tag(choice) }
                    } label: {
                        Label(language.text(.appearance), systemImage: "circle.lefthalf.filled")
                    }
                    .pickerStyle(.menu)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                }
                .modifier(LabPanel())
            }

            DisclosureGroup(isExpanded: $diagnostics) {
                VStack(alignment: .leading, spacing: 11) {
                    LabFact(label: language.text(.port), value: "127.0.0.1:43123", monospaced: true)
                    LabFact(label: language.text(.revision), value: "ULAB1-20261124-R1", monospaced: true)
                    LabFact(label: language.text(.status), value: model.connection.code, monospaced: true)
                    LabFact(label: language.text(.activity), value: language.text(model.connection.paused ? .queuePaused : .queueRunning))
                    Text(language.text(model.synthetic ? .testOnly : .connectionScope))
                        .font(.footnote)
                        .foregroundStyle(LabPalette.muted)
                }
                .padding(.top, 12)
            } label: {
                Label(language.text(.diagnostics), systemImage: "waveform.path")
                    .font(.headline)
                    .frame(minHeight: 44)
            }
            .modifier(LabPanel())

            Text(language.text(.localStorage))
                .font(.footnote)
                .foregroundStyle(LabPalette.muted)
                .padding(.bottom, 12)
        }
        .navigationTitle(language.text(.settings))
        .sheet(isPresented: $pairing) { PairingScreen(language: language) }
        .confirmationDialog(language.text(.forgetPairing), isPresented: $forgetting, titleVisibility: .visible) {
            Button(language.text(.forgetPairing), role: .destructive) {
                Task { _ = await model.setSecret(nil) }
            }
            Button(language.text(.back), role: .cancel) {}
        } message: {
            Text(language.text(.forgetDescription))
        }
    }
}

struct PairingScreen: View {
    let language: LabLanguage

    @EnvironmentObject private var model: LabModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @State private var typed = ""
    @State private var generated: Data?
    @State private var copied = false
    @State private var confirming = false
    @State private var keyError = false
    @ScaledMetric(relativeTo: .body) private var keyColumn: CGFloat = 90

    private var entered: Data? { Self.parse(typed) }
    private var candidate: Data? { generated ?? entered }

    var body: some View {
        NavigationStack {
            LabScroll {
                Text(language.text(.pairingExplanation))
                    .font(.body)
                    .foregroundStyle(LabPalette.muted)
                LabSection(title: language.text(.enterSecret)) {
                    VStack(alignment: .leading, spacing: 8) {
                        SecureField(language.text(.secretPlaceholder), text: $typed)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                            .keyboardType(.asciiCapable)
                            .font(.body.monospaced())
                            .padding(10)
                            .background(LabPalette.inset, in: RoundedRectangle(cornerRadius: 10))
                            .privacySensitive()
                            .accessibilityIdentifier("pairing-key-entry")
                            .onChange(of: typed) { _, value in
                                if !value.isEmpty { generated = nil; copied = false }
                            }
                        if !typed.isEmpty && entered == nil {
                            Text(language.text(.secretValidation))
                                .font(.footnote)
                                .foregroundStyle(LabPalette.caution)
                        }
                    }
                    .modifier(LabPanel())
                }

                LabSection(title: language.text(.generatedSecret)) {
                    VStack(alignment: .leading, spacing: 10) {
                        if let generated {
                            let hex = Self.hex(generated)
                            LazyVGrid(columns: [GridItem(.adaptive(minimum: keyColumn), alignment: .leading)], alignment: .leading, spacing: 5) {
                                ForEach(Array(Self.groups(hex).enumerated()), id: \.offset) { index, segment in
                                    Text(segment).accessibilitySortPriority(Double(8 - index))
                                }
                            }
                            .font(.body.monospaced().weight(.semibold))
                            .privacySensitive()
                            Text(language.text(.generatedHelp))
                                .font(.footnote)
                                .foregroundStyle(LabPalette.muted)
                            Button {
                                UIPasteboard.general.setItems(
                                    [[UTType.plainText.identifier: hex]],
                                    options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(60)]
                                )
                                copied = true
                            } label: {
                                Label(language.text(copied ? .copied : .copySecret), systemImage: copied ? "checkmark" : "document.on.document")
                            }
                            .buttonStyle(LabFieldButton())
                        } else {
                            Button {
                                do {
                                    generated = try PairingKeychain.generate()
                                    typed = ""
                                    copied = false
                                } catch {
                                    keyError = true
                                }
                            } label: {
                                Label(language.text(.generateSecret), systemImage: "key.horizontal")
                            }
                            .buttonStyle(LabFieldButton())
                            .disabled(model.busy || model.synthetic)
                        }
                    }
                    .modifier(LabPanel())
                }

                Button {
                    guard let candidate else { return }
                    if model.connection.paired {
                        confirming = true
                    } else {
                        save(candidate)
                    }
                } label: {
                    Label(language.text(.savePairing), systemImage: "lock")
                }
                .buttonStyle(LabPrimaryButton())
                .disabled(candidate == nil || model.busy || model.synthetic)
            }
            .navigationTitle(language.text(.pairing))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(language.text(.dismiss)) { dismiss() }.disabled(model.busy)
                }
            }
            .confirmationDialog(language.text(.savePairing), isPresented: $confirming, titleVisibility: .visible) {
                Button(language.text(.savePairing)) { if let candidate { save(candidate) } }
                Button(language.text(.back), role: .cancel) {}
            } message: {
                Text(language.text(.replaceDescription))
            }
            .onChange(of: scenePhase) { _, phase in
                if phase != .active && !model.busy { clearInputs() }
            }
            .onDisappear { clearInputs() }
        }
        .interactiveDismissDisabled(model.busy)
        .alert(language.text(.pairFailed), isPresented: $keyError) {
            Button(language.text(.done), role: .cancel) {}
        }
    }

    private func save(_ secret: Data) {
        Task {
            if await model.setSecret(secret, reportFailure: false) {
                clearInputs()
                dismiss()
            } else {
                keyError = true
            }
        }
    }

    private func clearInputs() {
        typed = ""
        generated = nil
        copied = false
    }

    private static func parse(_ text: String) -> Data? {
        guard text.utf8.count == 64 else { return nil }
        var result = Data()
        var high: UInt8?
        for byte in text.utf8 {
            let digit: UInt8
            switch byte {
            case 48...57: digit = byte - 48
            case 65...70: digit = byte - 55
            case 97...102: digit = byte - 87
            default: return nil
            }
            if let first = high {
                result.append(first << 4 | digit)
                high = nil
            } else {
                high = digit
            }
        }
        return result
    }

    private static func hex(_ secret: Data) -> String {
        secret.map { String(format: "%02X", $0) }.joined()
    }

    private static func groups(_ value: String) -> [String] {
        let characters = Array(value)
        return stride(from: 0, to: characters.count, by: 8).map { offset in
            String(characters[offset..<min(offset + 8, characters.count)])
        }
    }
}
