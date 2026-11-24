import SwiftUI
import UIKit
import USBLinkCore

struct HistoryScreen: View {
    let language: LabLanguage
    @EnvironmentObject private var model: LabModel

    var body: some View {
        ScrollView {
            if model.items.isEmpty {
                Text(language.text(.noRecords))
                    .font(.body)
                    .foregroundStyle(LabPalette.muted)
                    .modifier(LabPanel())
                    .padding(16)
            } else {
                LazyVStack(spacing: 0) {
                    ForEach(Array(model.items.reversed())) { item in
                        TransferRowLink(item: item, language: language)
                        Divider().padding(.leading, 44)
                    }
                }
                .frame(maxWidth: 680)
                .background(LabPalette.surface, in: RoundedRectangle(cornerRadius: 14))
                .overlay {
                    RoundedRectangle(cornerRadius: 14)
                        .strokeBorder(LabPalette.stroke, lineWidth: 1)
                        .allowsHitTesting(false)
                }
                .padding(16)
                .frame(maxWidth: .infinity)
            }
        }
        .background(LabPalette.canvas)
        .navigationTitle(language.text(.history))
    }
}

struct TransferRowLink: View {
    let item: LabItem
    let language: LabLanguage
    @State private var previewText: String?

    var body: some View {
        NavigationLink(value: item.id) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: item.symbol)
                    .font(.body)
                    .foregroundStyle(LabPalette.teal)
                    .frame(width: 21, alignment: .leading)
                    .padding(.top, 2)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 5) {
                    Text(item.displayName(language))
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(LabPalette.ink)
                        .multilineTextAlignment(.leading)
                        .lineLimit(2)
                    if let previewText, !previewText.isEmpty {
                        Text(previewText)
                            .font(.subheadline)
                            .foregroundStyle(LabPalette.ink)
                            .multilineTextAlignment(.leading)
                            .lineLimit(2)
                    }
                    Label(language.text(item.direction == .incoming ? .incoming : .outgoing), systemImage: item.direction == .incoming ? "arrow.down.left" : "arrow.up.right")
                        .foregroundStyle(LabPalette.muted)
                        .font(.caption)
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 10) {
                            stateLabel
                                .fixedSize(horizontal: true, vertical: false)
                            modeLabel
                                .fixedSize(horizontal: true, vertical: false)
                        }
                        VStack(alignment: .leading, spacing: 4) {
                            stateLabel
                            modeLabel
                        }
                    }
                    if item.active {
                        TransferProgress(item: item, language: language)
                    } else if let total = item.total, item.kind != .text {
                        Text(language.bytes(total))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(LabPalette.muted)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(LabPalette.muted)
                    .padding(.top, 4)
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("item-\(item.state.rawValue)")
        .task(id: item.fileURL) {
            guard item.kind == .text, let url = item.fileURL else {
                previewText = nil
                return
            }
            let text = await Task.detached(priority: .utility) { AttachmentFiles.messageBody(url, excerpt: true) }.value
            if !Task.isCancelled { previewText = text }
        }
    }

    private var stateLabel: some View {
        Text(language.text(item.status))
            .font(.caption.weight(.semibold))
            .foregroundStyle(item.needsAttention ? LabPalette.caution : LabPalette.ink)
    }

    private var modeLabel: some View {
        Label(item.mode.title, systemImage: item.mode.symbol)
            .font(.caption.weight(.medium))
            .foregroundStyle(item.mode.confidential ? LabPalette.teal : LabPalette.caution)
    }
}

private struct TransferProgress: View {
    let item: LabItem
    let language: LabLanguage

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let total = item.total {
                let ratio = total == 0 ? 1 : min(1, Double(item.bytes) / Double(total))
                ProgressView(value: ratio)
                    .tint(LabPalette.teal)
                    .accessibilityLabel(language.text(.transferred))
                    .accessibilityValue(language.percent(ratio))
                Text(language.format(.progressBytes, language.bytes(item.bytes), language.bytes(total)))
                    .font(.caption.monospacedDigit())
            } else {
                ProgressView()
                    .tint(LabPalette.teal)
                    .accessibilityLabel(language.text(item.status))
                Text(language.bytes(item.bytes))
                    .font(.caption.monospacedDigit())
            }
        }
        .foregroundStyle(LabPalette.muted)
        .padding(.top, 2)
    }
}

struct ItemDetail: View {
    let id: UUID
    let language: LabLanguage

    @EnvironmentObject private var model: LabModel
    @Environment(\.dismiss) private var dismiss
    @State private var preview: Image?
    @State private var previewFinished = false
    @State private var bodyText: String?
    @State private var retrying = false
    @State private var retryQueued = false
    @State private var stopping = false
    @State private var exporting = false
    @State private var export: ExportAttachment?
    @State private var saved = false

    private var item: LabItem? { model.items.first { $0.id == id } }

    var body: some View {
        LabScroll {
            if let item {
                summary(item)
                content(item)
                actions(item)
                if retryQueued && item.state == .queued {
                    Text(language.text(.retryQueued))
                        .font(.footnote)
                        .foregroundStyle(LabPalette.muted)
                }
            } else {
                Text(language.text(.detailsUnknown)).foregroundStyle(LabPalette.muted)
            }
        }
        .navigationTitle(language.text(.details))
        .navigationBarTitleDisplayMode(.inline)
        .task(id: item?.fileURL) { await loadContent(item) }
        .confirmationDialog(language.format(.retryTitle, item?.mode.title ?? ""), isPresented: $retrying, titleVisibility: .visible) {
            Button(language.text(.retry)) { Task { retryQueued = await model.retry(id) } }
            Button(language.text(.back), role: .cancel) {}
        } message: {
            if item?.state == .unconfirmed {
                Text(language.text(.retryDescription) + "\n\n" + language.text(.duplicateWarning))
            } else {
                Text(language.text(.retryDescription))
            }
        }
        .confirmationDialog(language.text(.stopSession), isPresented: $stopping, titleVisibility: .visible) {
            Button(language.text(.stopSession), role: .destructive) { Task { await model.cancelActive() } }
            Button(language.text(.keepGoing), role: .cancel) {}
        } message: {
            Text(language.text(.stopDescription))
        }
        .sheet(item: $export) { result in
            if result.share {
                ShareAttachmentController(attachment: result) { export = nil }
            } else {
                SaveAttachmentController(attachment: result) { didSave in
                    if didSave { saved = true }
                    export = nil
                }
            }
        }
    }

    private func summary(_ item: LabItem) -> some View {
        LabSection(title: language.text(.summary)) {
            VStack(alignment: .leading, spacing: 13) {
                Label(item.displayName(language), systemImage: item.symbol)
                    .font(.title3.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                LabFact(label: language.text(.direction), value: language.text(item.direction == .incoming ? .incoming : .outgoing))
                LabFact(label: language.text(.status), value: language.text(item.status))
                if item.active {
                    TransferProgress(item: item, language: language)
                    if item.state == .sending, let total = item.total, item.bytes == total {
                        Text(language.text(.acknowledgePending)).font(.footnote).foregroundStyle(LabPalette.muted)
                    }
                }
                LabFact(label: language.text(.transferred), value: item.bytes.formatted(.number.locale(language.locale)))
                if let total = item.total {
                    LabFact(label: language.text(.total), value: total.formatted(.number.locale(language.locale)))
                }
                if let mediaType = item.mediaType {
                    LabFact(label: language.text(.mediaType), value: mediaType)
                }
                Divider()
                LabFact(label: language.text(.protection), value: item.mode.title)
                LabFact(label: language.text(.confidentiality), value: language.text(item.mode.confidential ? .yes : .no))
                LabFact(label: language.text(.authenticity), value: language.text(item.mode.authentic ? .yes : .no))
                ExposureLabel(mode: item.mode, language: language)
                if item.state == .completed {
                    let completion: LabCopy = item.direction == .outgoing
                        ? .completeRemote
                        : (item.mode == .plaintext ? .completePlaintext : .completeLocal)
                    Text(language.text(completion))
                        .font(.footnote)
                        .foregroundStyle(LabPalette.muted)
                } else if let action = LabConnection.recovery(item.detail) {
                    Text(language.text(action))
                        .font(.footnote.weight(.medium))
                        .foregroundStyle(LabPalette.caution)
                }
            }
            .modifier(LabPanel())
        }
    }

    @ViewBuilder
    private func content(_ item: LabItem) -> some View {
        if item.kind == .text, let bodyText, !bodyText.isEmpty {
            LabSection(title: language.text(.message)) {
                Text(bodyText)
                    .font(.body)
                    .textSelection(.enabled)
                    .modifier(LabPanel())
            }
        } else if item.downloadable && item.kind == .image {
            LabSection(title: language.text(.preview)) {
                Group {
                    if let preview {
                        preview.resizable()
                            .scaledToFit()
                            .frame(maxHeight: 420)
                            .accessibilityLabel(language.text(.previewLabel))
                    } else if previewFinished {
                        Label(language.text(.previewUnavailable), systemImage: "photo")
                            .font(.subheadline)
                            .foregroundStyle(LabPalette.muted)
                    } else {
                        ProgressView()
                            .accessibilityLabel(language.text(.preview))
                    }
                }
                .modifier(LabPanel())
            }
        }
    }

    @ViewBuilder
    private func actions(_ item: LabItem) -> some View {
        if item.active {
            Button(role: .destructive) { stopping = true } label: {
                Label(language.text(.stopSession), systemImage: "stop.circle")
            }
            .buttonStyle(LabFieldButton())
        } else if item.state == .queued {
            Button(role: .destructive) {
                Task {
                    if await model.remove(id) { dismiss() }
                }
            } label: {
                Label(language.text(.remove), systemImage: "trash")
            }
            .buttonStyle(LabFieldButton())
            .disabled(model.busy)
        } else if item.canRetry {
            if item.state == .unconfirmed {
                Text(language.text(.duplicateWarning))
                    .font(.subheadline)
                    .foregroundStyle(LabPalette.caution)
            }
            Button {
                retrying = true
            } label: {
                Label(language.format(.retryTitle, item.mode.title), systemImage: "arrow.clockwise")
            }
            .buttonStyle(LabPrimaryButton())
            .disabled(model.busy || !model.connection.paired)
        } else if item.downloadable {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    exportButton(item, share: false)
                    exportButton(item, share: true)
                }
                VStack(alignment: .leading, spacing: 8) {
                    exportButton(item, share: false)
                    exportButton(item, share: true)
                }
            }
            if exporting {
                ProgressView().accessibilityLabel(language.text(.sourceReading))
            } else if saved {
                Label(language.text(.savedFiles), systemImage: "checkmark")
                    .font(.subheadline)
                    .foregroundStyle(LabPalette.teal)
            }
        }
    }

    private func exportButton(_ item: LabItem, share: Bool) -> some View {
        Button {
            beginExport(item, share: share)
        } label: {
            Label(language.text(share ? .share : .saveFiles), systemImage: share ? "square.and.arrow.up" : "folder")
                .fixedSize(horizontal: true, vertical: false)
        }
        .buttonStyle(LabFieldButton())
        .disabled(exporting)
    }

    private func loadContent(_ item: LabItem?) async {
        guard let item, let url = item.fileURL else { return }
        if item.kind == .text {
            let text = await Task.detached(priority: .utility) { AttachmentFiles.messageBody(url, excerpt: false) }.value
            if !Task.isCancelled { bodyText = text }
        } else if item.downloadable && item.kind == .image {
            let pixels = await Task.detached(priority: .utility) { AttachmentFiles.thumbnail(url) }.value
            guard !Task.isCancelled else { return }
            preview = pixels.map { Image(uiImage: UIImage(cgImage: $0)) }
            previewFinished = true
        }
    }

    private func beginExport(_ item: LabItem, share: Bool) {
        guard let url = item.fileURL, let name = item.name, !exporting else { return }
        exporting = true
        Task {
            defer { exporting = false }
            do {
                export = try await Task.detached(priority: .userInitiated) {
                    try AttachmentFiles.export(url: url, name: name, share: share)
                }.value
            } catch {
                model.report(.exportFailed)
            }
        }
    }
}

private struct SaveAttachmentController: UIViewControllerRepresentable {
    let attachment: ExportAttachment
    let finished: (Bool) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(finished: finished) }

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(forExporting: [attachment.url], asCopy: true)
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let finished: (Bool) -> Void

        init(finished: @escaping (Bool) -> Void) { self.finished = finished }

        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
            finished(!urls.isEmpty)
        }

        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
            finished(false)
        }
    }
}

private struct ShareAttachmentController: UIViewControllerRepresentable {
    let attachment: ExportAttachment
    let finished: () -> Void

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let controller = UIActivityViewController(activityItems: [attachment.url], applicationActivities: nil)
        controller.completionWithItemsHandler = { _, _, _, _ in finished() }
        return controller
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
