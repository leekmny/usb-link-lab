import Foundation
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers
import USBLinkCore

struct TransferScreen: View {
    let language: LabLanguage
    let showHistory: () -> Void

    @EnvironmentObject private var model: LabModel
    @FocusState private var composing: Bool
    @State private var message = ""
    @State private var mode: ProtectionMode = .aes256GCM
    @State private var pairing = false
    @State private var showImporter = false
    @State private var photo: PhotosPickerItem?
    @State private var preparation: Task<Void, Never>?
    @State private var preparationID: UUID?
    @State private var pending: PendingAttachment?
    @State private var stopping = false

    private var mayQueue: Bool { model.ready && model.connection.paired && !model.busy && !model.synthetic }
    private var textTooLarge: Bool { message.utf8.count > LabLimits.text }
    private var active: [LabItem] { model.items.filter(\.active) }
    private var queued: [LabItem] { model.items.filter { $0.state == .queued } }
    private var attention: LabItem? { model.items.last(where: \.needsAttention) }

    var body: some View {
        LabScroll {
            ConnectionPanel(language: language, openPairing: { pairing = true })
            composer
            activity
        }
        .navigationTitle("USBLinkLab")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button(language.text(.hideKeyboard)) { composing = false }
            }
        }
        .sheet(isPresented: $pairing) { PairingScreen(language: language) }
        .sheet(item: $pending) { choice in
            AttachmentConfirmation(choice: choice, language: language) {
                let queued = await model.enqueueAttachment(choice.file, mode: choice.mode)
                if queued { pending = nil }
                return queued
            }
            .presentationDragIndicator(.visible)
        }
        .fileImporter(isPresented: $showImporter, allowedContentTypes: [.item]) { result in
            switch result {
            case let .success(url): prepareDocument(url)
            case let .failure(error):
                if (error as NSError).code != NSUserCancelledError { model.report(.sourceUnavailable) }
            }
        }
        .onChange(of: photo) { _, item in
            guard let item else { return }
            photo = nil
            preparePhoto(item)
        }
        .confirmationDialog(language.text(.stopSession), isPresented: $stopping, titleVisibility: .visible) {
            Button(language.text(.stopSession), role: .destructive) { Task { await model.cancelActive() } }
            Button(language.text(.keepGoing), role: .cancel) {}
        } message: {
            Text(language.text(.stopDescription))
        }
    }

    private var composer: some View {
        LabSection(title: language.text(.compose)) {
            VStack(alignment: .leading, spacing: 11) {
                TextField(language.text(.draftPlaceholder), text: $message, axis: .vertical)
                    .lineLimit(2...5)
                    .font(.body)
                    .focused($composing)
                    .padding(11)
                    .background(LabPalette.inset, in: RoundedRectangle(cornerRadius: 10))
                    .accessibilityIdentifier("message-draft")

                if textTooLarge {
                    Text(language.text(.textLimit))
                        .font(.footnote)
                        .foregroundStyle(LabPalette.caution)
                }

                ModeSelector(language: language, selection: $mode)
                ExposureLabel(mode: mode, language: language)

                Divider().overlay(LabPalette.stroke)
                let chosen = mode
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) {
                        photoButton
                        fileButton
                        sendButton(chosen)
                            .fixedSize(horizontal: true, vertical: false)
                    }
                    VStack(spacing: 8) {
                        HStack(spacing: 8) {
                            photoButton
                            fileButton
                            Spacer(minLength: 0)
                        }
                        sendButton(chosen)
                    }
                }
                if preparationID != nil {
                    HStack {
                        ProgressView()
                        Text(language.text(.sourceReading))
                            .font(.footnote)
                            .foregroundStyle(LabPalette.muted)
                        Spacer(minLength: 4)
                        Button(language.text(.back)) { cancelPreparation() }
                            .frame(minHeight: 44)
                    }
                }
            }
            .modifier(LabPanel())
        }
    }

    private var photoButton: some View {
        PhotosPicker(selection: $photo, matching: .images, preferredItemEncoding: .current) {
            Label(language.text(.photo), systemImage: "photo")
                .font(.subheadline.weight(.medium))
                .fixedSize(horizontal: true, vertical: false)
        }
        .buttonStyle(LabFieldButton())
        .disabled(!mayQueue || preparationID != nil)
        .accessibilityIdentifier("pick-photo")
    }

    private var fileButton: some View {
        Button {
            composing = false
            showImporter = true
        } label: {
            Label(language.text(.file), systemImage: "doc")
                .font(.subheadline.weight(.medium))
                .fixedSize(horizontal: true, vertical: false)
        }
        .buttonStyle(LabFieldButton())
        .disabled(!mayQueue || preparationID != nil)
        .accessibilityIdentifier("pick-file")
    }

    private func sendButton(_ mode: ProtectionMode) -> some View {
        Button {
            let content = message
            composing = false
            Task {
                if await model.enqueueText(content, mode: mode), message == content { message = "" }
            }
        } label: {
            Label(language.text(.addToQueue), systemImage: "arrow.up")
                .font(.subheadline.weight(.semibold))
        }
        .buttonStyle(LabPrimaryButton())
        .disabled(!mayQueue || message.isEmpty || textTooLarge)
        .accessibilityHint(language.text(.draftHint))
        .accessibilityIdentifier("enqueue-message")
    }

    private var activity: some View {
        LabSection(title: language.text(.activity)) {
            if active.isEmpty && queued.isEmpty && attention == nil {
                Text(language.text(.noActivity))
                    .font(.subheadline)
                    .foregroundStyle(LabPalette.muted)
                    .modifier(LabPanel())
            } else {
                VStack(spacing: 0) {
                    ForEach(active + Array(queued.prefix(3))) { item in
                        TransferRowLink(item: item, language: language)
                        Divider().padding(.leading, 42)
                    }
                    if let attention {
                        TransferRowLink(item: attention, language: language)
                        Divider().padding(.leading, 42)
                    }
                    if !active.isEmpty {
                        Button(role: .destructive) { stopping = true } label: {
                            Label(language.text(.stopSession), systemImage: "stop.circle")
                                .font(.subheadline.weight(.semibold))
                                .frame(minHeight: 44)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .foregroundStyle(LabPalette.caution)
                        .padding(.horizontal, 12)
                    }
                    Button(action: showHistory) {
                        Label(language.text(.allHistory), systemImage: "clock.arrow.circlepath")
                            .font(.subheadline.weight(.semibold))
                            .frame(minHeight: 44)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.horizontal, 12)
                }
                .background(LabPalette.surface, in: RoundedRectangle(cornerRadius: 14))
                .overlay {
                    RoundedRectangle(cornerRadius: 14)
                        .strokeBorder(LabPalette.stroke, lineWidth: 1)
                        .allowsHitTesting(false)
                }
            }
        }
    }

    private func preparePhoto(_ item: PhotosPickerItem) {
        prepare {
            guard let loaded = try await item.loadTransferable(type: PhotoRepresentation.self) else {
                throw AttachmentFailure.unavailable
            }
            return loaded.attachment
        }
    }

    private func prepareDocument(_ url: URL) {
        prepare {
            let worker = Task.detached(priority: .userInitiated) { try AttachmentFiles.stageDocument(url) }
            return try await withTaskCancellationHandler {
                try await worker.value
            } onCancel: {
                worker.cancel()
            }
        }
    }

    private func prepare(_ load: @escaping () async throws -> PreparedAttachment) {
        let id = UUID()
        let protection = mode
        cancelPreparation()
        preparationID = id
        preparation = Task {
            do {
                let staged = try await load()
                try Task.checkCancellation()
                if preparationID == id { pending = PendingAttachment(file: staged, mode: protection) }
            } catch {
                if preparationID == id, !Task.isCancelled {
                    model.report((error as? AttachmentFailure)?.message ?? .sourceUnavailable)
                }
            }
            if preparationID == id {
                preparationID = nil
                preparation = nil
            }
        }
    }

    private func cancelPreparation() {
        preparation?.cancel()
        preparation = nil
        preparationID = nil
    }
}

private struct ConnectionPanel: View {
    let language: LabLanguage
    let openPairing: () -> Void
    @EnvironmentObject private var model: LabModel

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(language.text(.connection), systemImage: "cable.connector")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(LabPalette.softWhite)
                .accessibilityAddTraits(.isHeader)
            Label(language.text(model.connection.label), systemImage: model.connection.connected ? "checkmark.circle.fill" : "circle.dotted")
                .font(.title2.bold())
                .foregroundStyle(model.connection.connected ? LabPalette.signal : .white)
                .accessibilityLabel(language.text(model.connection.label))
                .accessibilityIdentifier("connection-status")
            Text(language.text(model.connection.message))
                .font(.subheadline)
                .foregroundStyle(LabPalette.softWhite)
                .fixedSize(horizontal: false, vertical: true)

            if model.ready && !model.connection.paired {
                Button(action: openPairing) {
                    Label(language.text(.setupLink), systemImage: "key.horizontal")
                }
                .buttonStyle(LabPrimaryButton())
                .accessibilityIdentifier("open-pairing")
            } else if model.connection.paused {
                Button {
                    Task { await model.resume() }
                } label: {
                    Label(language.text(.resumeQueue), systemImage: "play")
                }
                .buttonStyle(LabPrimaryButton())
                .disabled(model.busy)
                .accessibilityHint(language.text(.pausedInstruction))
            }
        }
        .padding(15)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LabPalette.navy, in: RoundedRectangle(cornerRadius: 14))
    }
}

struct ModeSelector: View {
    let language: LabLanguage
    @Binding var selection: ProtectionMode

    var body: some View {
        Menu {
            ForEach(ProtectionMode.choices, id: \.rawValue) { mode in
                Button {
                    selection = mode
                } label: {
                    Label(mode.title, systemImage: selection == mode ? "checkmark" : mode.symbol)
                }
            }
        } label: {
            ViewThatFits(in: .horizontal) {
                HStack {
                    Text(language.text(.protection))
                        .fixedSize(horizontal: true, vertical: false)
                    Spacer(minLength: 10)
                    selectedMode
                        .fixedSize(horizontal: true, vertical: false)
                }
                VStack(alignment: .leading, spacing: 5) {
                    Text(language.text(.protection))
                    selectedMode
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .font(.subheadline)
            .foregroundStyle(LabPalette.ink)
            .padding(.horizontal, 11)
            .padding(.vertical, 8)
            .frame(minHeight: 44)
            .background(LabPalette.inset, in: RoundedRectangle(cornerRadius: 10))
        }
        .accessibilityLabel(language.text(.protection))
        .accessibilityValue(selection.title)
        .accessibilityIdentifier("draft-protection")
    }

    private var selectedMode: some View {
        HStack(spacing: 7) {
            Text(selection.title)
                .fontWeight(.semibold)
            Image(systemName: "chevron.up.chevron.down")
                .font(.caption2.weight(.semibold))
                .accessibilityHidden(true)
        }
        .foregroundStyle(LabPalette.teal)
    }
}

struct ExposureLabel: View {
    let mode: ProtectionMode
    let language: LabLanguage

    var body: some View {
        HStack(alignment: .top, spacing: 7) {
            Image(systemName: mode.symbol)
                .accessibilityHidden(true)
            Text(language.text(mode.exposure))
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("draft-exposure")
        }
        .font(.footnote.weight(.medium))
        .foregroundStyle(mode.confidential ? LabPalette.teal : LabPalette.caution)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(9)
        .background(mode.confidential ? LabPalette.tealSurface : LabPalette.warningSurface, in: RoundedRectangle(cornerRadius: 9))
    }
}

private struct PendingAttachment: Identifiable {
    let file: PreparedAttachment
    let mode: ProtectionMode
    var id: UUID { file.id }
}

private struct AttachmentConfirmation: View {
    let choice: PendingAttachment
    let language: LabLanguage
    let confirm: () async -> Bool
    @EnvironmentObject private var model: LabModel
    @Environment(\.dismiss) private var dismiss
    @State private var failed = false

    var body: some View {
        NavigationStack {
            LabScroll {
                VStack(alignment: .leading, spacing: 12) {
                    Label(choice.file.name, systemImage: choice.file.kind == .image ? "photo" : "doc")
                        .font(.headline)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(language.format(.exactBytes, language.bytes(choice.file.size), choice.file.size.formatted(.number.locale(language.locale))))
                        .font(.subheadline.monospacedDigit())
                    Text(language.text(choice.file.kind == .image ? .selectedPhoto : .selectedFile))
                        .font(.subheadline)
                        .foregroundStyle(LabPalette.muted)
                    LabFact(label: language.text(.mediaType), value: choice.file.mediaType)
                    Divider()
                    LabFact(label: language.text(.protection), value: choice.mode.title)
                    ExposureLabel(mode: choice.mode, language: language)
                }
                .modifier(LabPanel())
                Button {
                    Task { failed = !(await confirm()) }
                } label: {
                    Label(language.text(model.busy ? .queueBusy : .addToQueue), systemImage: "arrow.up")
                }
                .buttonStyle(LabPrimaryButton())
                .disabled(model.busy)
            }
            .navigationTitle(language.text(.attachmentConfirm))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(language.text(.back)) { dismiss() }.disabled(model.busy) }
            }
        }
        .interactiveDismissDisabled(model.busy)
        .alert(language.text(.queueFailed), isPresented: $failed) {
            Button(language.text(.done), role: .cancel) {}
        }
    }
}
