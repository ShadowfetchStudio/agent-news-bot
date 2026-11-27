import SwiftUI
import AppKit

@main
struct AgentNewsBotApp: App {
    @ObservedObject private var engine = NewsEngine.shared
    @Environment(\.openWindow) private var openWindow

    init() {
        NSApplication.shared.appearance = NSAppearance(named: .darkAqua)
    }

    var body: some Scene {
        Window("Agent News Bot", id: "desk") {
            ContentView()
                .environmentObject(engine)
                .preferredColorScheme(.dark)
                .frame(minWidth: 920, minHeight: 620)
                .onAppear { engine.start() }
        }
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Open News Desk") {
                    openWindow(id: "desk")
                    NSApp.activate(ignoringOtherApps: true)
                }
                .keyboardShortcut("n", modifiers: [.command])
            }
            CommandMenu("Desk") {
                Button("Fetch Fresh") {
                    Task { await engine.fetchFresh() }
                }
                .keyboardShortcut("r", modifiers: [.command])
                .disabled(engine.isFetching)
                Button("Bookmark Selected Story") {
                    if let article = engine.selected ?? engine.filtered.first {
                        engine.toggleBookmark(article)
                    }
                }
                .keyboardShortcut("b", modifiers: [.command])
                .disabled((engine.selected ?? engine.filtered.first) == nil)
                Button("Copy Selected Tweet") {
                    if let article = engine.selected ?? engine.filtered.first {
                        engine.copyTweet(article)
                    }
                }
                .keyboardShortcut("c", modifiers: [.command, .shift])
                .disabled({
                    guard let a = engine.selected ?? engine.filtered.first else { return true }
                    return a.tweet.isEmpty || a.tweetChars > 280
                }())
                Divider()
                Button("Send Selection to Hermes") {
                    Task { await engine.sendToHermes() }
                }
                .keyboardShortcut("h", modifiers: [.command, .shift])
                .disabled(engine.agentTestRunning || !engine.hermes.canSend || (engine.selected ?? engine.filtered.first) == nil)
                Button("Send Selection to OpenClaw") {
                    Task { await engine.sendToOpenClaw() }
                }
                .disabled(engine.agentTestRunning || !engine.openclaw.canSend || (engine.selected ?? engine.filtered.first) == nil)
                Divider()
                Button("Reveal Exports") {
                    engine.revealExports()
                }
            }
        }

        Settings {
            SettingsView()
                .environmentObject(engine)
                .frame(width: 560, height: 620)
        }

        MenuBarExtra("Agent News Bot", systemImage: "dot.radiowaves.left.and.right") {
            Button(engine.isFetching ? "Fetching…" : "Fetch Fresh") {
                Task { await engine.fetchFresh() }
            }
            .disabled(engine.isFetching)
            Text("\(engine.articles.count) displayed")
            Divider()
            Button("Open Desk") {
                openWindow(id: "desk")
                NSApp.activate(ignoringOtherApps: true)
            }
            Button("Quit") { NSApp.terminate(nil) }
        }
    }
}
