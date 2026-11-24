import SwiftUI
import UIKit
import USBLinkCore

@main
@MainActor
struct USBLinkLabApp: App {
    @StateObject private var model = LabModel()
    @AppStorage("language") private var language: LabLanguage = .ko
    @AppStorage("appearance") private var appearance: LabAppearance = .system
    @Environment(\.scenePhase) private var scenePhase

    private var shownLanguage: LabLanguage { model.synthetic ? .ko : language }

    private var shownAppearance: ColorScheme? {
        #if DEBUG && targetEnvironment(simulator)
        if model.synthetic {
            if ProcessInfo.processInfo.arguments.contains("--lab-dark") { return .dark }
            if ProcessInfo.processInfo.arguments.contains("--lab-light") { return .light }
        }
        #endif
        return appearance.scheme
    }

    var body: some Scene {
        WindowGroup {
            LabRoot(language: shownLanguage, selectedLanguage: $language, appearance: $appearance)
                .environmentObject(model)
                .environment(\.locale, shownLanguage.locale)
                .preferredColorScheme(shownAppearance)
                .tint(LabPalette.teal)
                .task { await model.connect() }
                .onChange(of: scenePhase, initial: true) { _, phase in
                    Task { await model.setForeground(phase == .active) }
                }
        }
    }
}

enum LabAppearance: String, CaseIterable, Identifiable {
    case system, light, dark

    var id: String { rawValue }

    var scheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }

    var word: LabCopy {
        switch self {
        case .system: .system
        case .light: .light
        case .dark: .dark
        }
    }
}

private struct LabRoot: View {
    private enum Page { case transfer, history, settings }

    let language: LabLanguage
    @Binding var selectedLanguage: LabLanguage
    @Binding var appearance: LabAppearance
    @State private var page = Page.transfer
    @EnvironmentObject private var model: LabModel

    var body: some View {
        TabView(selection: $page) {
            NavigationStack {
                TransferScreen(language: language, showHistory: { page = .history })
                    .navigationDestination(for: UUID.self) { id in ItemDetail(id: id, language: language) }
            }
            .tabItem { Label(language.text(.workbench), systemImage: "arrow.up.arrow.down") }
            .tag(Page.transfer)

            NavigationStack {
                HistoryScreen(language: language)
                    .navigationDestination(for: UUID.self) { id in ItemDetail(id: id, language: language) }
            }
            .tabItem { Label(language.text(.history), systemImage: "clock") }
            .tag(Page.history)

            NavigationStack {
                SettingsScreen(language: language, selectedLanguage: $selectedLanguage, appearance: $appearance)
            }
            .tabItem { Label(language.text(.settings), systemImage: "gearshape") }
            .tag(Page.settings)
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            if model.synthetic {
                HStack(spacing: 6) {
                    Image(systemName: "testtube.2").accessibilityHidden(true)
                    Text(language.text(.synthetic)).accessibilityIdentifier("synthetic-label")
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(LabPalette.caution)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 7)
                .padding(.horizontal, 12)
                .background(LabPalette.warningSurface)
            }
        }
        .alert(item: $model.problem) { problem in
            Alert(title: Text(language.text(problem.message)), dismissButton: .default(Text(language.text(.done))))
        }
    }
}

enum LabPalette {
    static let canvas = adaptive(light: 0xF2F6F8, dark: 0x0A1422)
    static let surface = adaptive(light: 0xFFFFFF, dark: 0x121F30)
    static let inset = adaptive(light: 0xE9F0F3, dark: 0x1A2A3E)
    static let ink = adaptive(light: 0x10243A, dark: 0xEDF5FA)
    static let muted = adaptive(light: 0x485D70, dark: 0xB3C5D4)
    static let stroke = adaptive(light: 0xCCD9E0, dark: 0x354A60)
    static let teal = adaptive(light: 0x00685F, dark: 0x65E3CC)
    static let tealSurface = adaptive(light: 0xDEF1EC, dark: 0x103933)
    static let caution = adaptive(light: 0x774514, dark: 0xFFD199)
    static let warningSurface = adaptive(light: 0xFDF0DF, dark: 0x422E20)
    static let navy = Color(hex: 0x11263B)
    static let signal = Color(hex: 0x65E3CC)
    static let softWhite = Color(hex: 0xCDDCE7)

    private static func adaptive(light: UInt32, dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in
            UIColor(hex: traits.userInterfaceStyle == .dark ? dark : light)
        })
    }
}

private extension UIColor {
    convenience init(hex: UInt32) {
        self.init(
            red: CGFloat((hex >> 16) & 255) / 255,
            green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255,
            alpha: 1
        )
    }
}

private extension Color {
    init(hex: UInt32) { self.init(uiColor: UIColor(hex: hex)) }
}

struct LabSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.headline)
                .foregroundStyle(LabPalette.ink)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct LabPanel: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(LabPalette.surface, in: RoundedRectangle(cornerRadius: 14))
            .overlay {
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(LabPalette.stroke, lineWidth: 1)
                    .allowsHitTesting(false)
            }
    }
}

struct LabScroll<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) { content }
                .frame(maxWidth: 680)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(LabPalette.canvas)
        .foregroundStyle(LabPalette.ink)
    }
}

struct LabFact: View {
    let label: String
    let value: String
    var monospaced = false

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(label)
                    .foregroundStyle(LabPalette.muted)
                    .fixedSize(horizontal: true, vertical: false)
                Spacer(minLength: 12)
                factValue
                    .fixedSize(horizontal: true, vertical: false)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(label)
                    .foregroundStyle(LabPalette.muted)
                factValue
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.subheadline)
        .accessibilityElement(children: .combine)
    }

    private var factValue: some View {
        Text(value)
            .fontDesign(monospaced ? .monospaced : .default)
            .foregroundStyle(LabPalette.ink)
            .textSelection(.enabled)
    }
}

struct LabPrimaryButton: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .multilineTextAlignment(.center)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, minHeight: 46)
            .foregroundStyle(isEnabled ? LabPalette.navy : LabPalette.muted)
            .background(isEnabled ? LabPalette.signal : LabPalette.inset, in: RoundedRectangle(cornerRadius: 11))
            .opacity(configuration.isPressed ? 0.76 : 1)
    }
}

struct LabFieldButton: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.horizontal, 11)
            .padding(.vertical, 8)
            .frame(minHeight: 44)
            .foregroundStyle(isEnabled ? LabPalette.ink : LabPalette.muted)
            .background(LabPalette.inset, in: RoundedRectangle(cornerRadius: 10))
            .opacity(configuration.isPressed ? 0.72 : 1)
    }
}
