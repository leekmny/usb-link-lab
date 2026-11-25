import CoreTransferable
import Foundation
import ImageIO
import UniformTypeIdentifiers
import USBLinkCore

enum AttachmentFailure: Error {
    case unavailable, tooLarge, photoFormat

    var message: LabCopy {
        switch self {
        case .unavailable: .sourceUnavailable
        case .tooLarge: .sourceTooLarge
        case .photoFormat: .sourcePhotoFormat
        }
    }
}

final class PreparedAttachment: Identifiable, @unchecked Sendable {
    let id = UUID()
    let url: URL
    let kind: ItemKind
    let name: String
    let mediaType: String
    let size: UInt64

    init(url: URL, kind: ItemKind, name: String, mediaType: String, size: UInt64) {
        self.url = url
        self.kind = kind
        self.name = name
        self.mediaType = mediaType
        self.size = size
    }

    deinit { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
}

struct PhotoRepresentation: Transferable {
    let attachment: PreparedAttachment

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .image) { received in
            PhotoRepresentation(attachment: try AttachmentFiles.stagePhoto(received.file))
        }
    }
}

final class ExportAttachment: Identifiable, @unchecked Sendable {
    let id = UUID()
    let url: URL
    let share: Bool

    init(url: URL, share: Bool) {
        self.url = url
        self.share = share
    }

    deinit { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
}

enum AttachmentFiles {
    private static var staging: URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("USBLinkLab-Staging", isDirectory: true)
    }

    static func purgeStaging() {
        try? FileManager.default.removeItem(at: staging)
    }

    static func stageDocument(_ url: URL) throws -> PreparedAttachment {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        var coordinatorError: NSError?
        var result: Result<PreparedAttachment, Error>?
        NSFileCoordinator().coordinate(readingItemAt: url, options: [], error: &coordinatorError) { resolved in
            result = Result {
                let name = try validatedFilename(url.lastPathComponent)
                let type = UTType(filenameExtension: url.pathExtension)
                return try stage(resolved, kind: .file, name: name, mediaType: type?.preferredMIMEType ?? "application/octet-stream")
            }
        }
        if let coordinatorError { throw coordinatorError }
        guard let result else { throw AttachmentFailure.unavailable }
        return try result.get()
    }

    static func stagePhoto(_ url: URL) throws -> PreparedAttachment {
        _ = try checkedSize(url)
        let options = [kCGImageSourceShouldCache: false] as CFDictionary
        guard let image = CGImageSourceCreateWithURL(url as CFURL, options),
              let identifier = CGImageSourceGetType(image) as String?,
              let type = UTType(identifier),
              type.conforms(to: .image),
              let mime = type.preferredMIMEType,
              mime.hasPrefix("image/") else {
            throw AttachmentFailure.photoFormat
        }
        let name = try validatedFilename(url.lastPathComponent)
        return try stage(url, kind: .image, name: name, mediaType: mime)
    }

    static func export(url: URL, name: String, share: Bool) throws -> ExportAttachment {
        let name = try validatedFilename(name)
        let folder = try freshDirectory()
        let destination = folder.appendingPathComponent(name, isDirectory: false)
        do {
            _ = try checkedSize(url)
            _ = try copy(url, to: destination)
            return ExportAttachment(url: destination, share: share)
        } catch {
            try? FileManager.default.removeItem(at: folder)
            throw error
        }
    }

    static func thumbnail(_ url: URL) -> CGImage? {
        guard let size = try? checkedSize(url), size <= 64 * 1_048_576 else { return nil }
        let noCache = [kCGImageSourceShouldCache: false] as CFDictionary
        guard let source = CGImageSourceCreateWithURL(url as CFURL, noCache),
              let identifier = CGImageSourceGetType(source) as String?,
              let type = UTType(identifier), type.conforms(to: .image),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, noCache) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Int,
              let height = properties[kCGImagePropertyPixelHeight] as? Int,
              width > 0, height > 0, max(width, height) <= 12_000,
              width * height <= 24_000_000 else { return nil }

        let thumbnail: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: 1024,
            kCGImageSourceShouldCacheImmediately: true
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, thumbnail as CFDictionary)
    }

    static func messageBody(_ url: URL, excerpt: Bool) -> String? {
        let maximum = excerpt ? 4_096 : LabLimits.text
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        var data = Data()
        do {
            while data.count <= maximum {
                let remaining = maximum + 1 - data.count
                guard let chunk = try handle.read(upToCount: min(remaining, 65_536)), !chunk.isEmpty else { break }
                data.append(chunk)
            }
        } catch {
            return nil
        }
        if data.count <= maximum { return String(data: data, encoding: .utf8) }
        guard excerpt else { return nil }

        // Avoid splitting a UTF-8 scalar at the excerpt boundary.
        var end = maximum
        while end > 0, data[end] & 0b1100_0000 == 0b1000_0000 { end -= 1 }
        guard let text = String(data: data.prefix(end), encoding: .utf8) else { return nil }
        return text + "…"
    }

    private static func stage(_ url: URL, kind: ItemKind, name: String, mediaType: String) throws -> PreparedAttachment {
        _ = try checkedSize(url)
        let folder = try freshDirectory()
        let destination = folder.appendingPathComponent("payload", isDirectory: false)
        do {
            let size = try copy(url, to: destination)
            return PreparedAttachment(url: destination, kind: kind, name: name, mediaType: mediaType, size: size)
        } catch {
            try? FileManager.default.removeItem(at: folder)
            throw error
        }
    }

    private static func copy(_ source: URL, to destination: URL) throws -> UInt64 {
        let input = try FileHandle(forReadingFrom: source)
        defer { try? input.close() }
        guard FileManager.default.createFile(atPath: destination.path, contents: nil, attributes: [.protectionKey: FileProtectionType.complete]) else {
            throw AttachmentFailure.unavailable
        }
        let output = try FileHandle(forWritingTo: destination)
        defer { try? output.close() }

        var total: UInt64 = 0
        while let chunk = try input.read(upToCount: 65_536), !chunk.isEmpty {
            try Task.checkCancellation()
            guard UInt64(chunk.count) <= LabLimits.attachment - total else { throw AttachmentFailure.tooLarge }
            try output.write(contentsOf: chunk)
            total += UInt64(chunk.count)
        }
        try output.synchronize()
        return total
    }

    private static func checkedSize(_ url: URL) throws -> UInt64 {
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
        guard values.isRegularFile == true, let size = values.fileSize, size >= 0 else {
            throw AttachmentFailure.unavailable
        }
        guard UInt64(size) <= LabLimits.attachment else { throw AttachmentFailure.tooLarge }
        return UInt64(size)
    }

    private static func freshDirectory() throws -> URL {
        let folder = staging.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }

    private static func validatedFilename(_ name: String) throws -> String {
        let disallowed = CharacterSet.controlCharacters.union(CharacterSet(charactersIn: "/\\"))
        guard !name.isEmpty, name != ".", name != "..", name.utf8.count <= 255,
              name.rangeOfCharacter(from: disallowed) == nil else {
            throw AttachmentFailure.unavailable
        }
        return name
    }
}
