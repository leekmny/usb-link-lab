import SwiftUI

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
                Task { await model.forgetPairing() }
            }
            Button(language.text(.back), role: .cancel) {}
        } message: {
            Text(language.text(.forgetDescription))
        }
    }
}
