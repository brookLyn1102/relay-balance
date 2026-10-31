import AppKit
import Foundation

/// 轻量日志：~/Library/Logs/relay-balance/app.log，单文件 1MB 轮转。
///
/// 和 Windows 版一个定位——把静默 catch 的地方留下一条痕迹，出问题时
/// 不用靠猜。只追加，不做级别和模块前缀。
enum Log {
    static let maxBytes = 1024 * 1024
    private static let lock = NSLock()

    private static let logURL: URL = {
        let fm = FileManager.default
        let base = fm.urls(for: .libraryDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory())
        let dir = base.appendingPathComponent("Logs/relay-balance", isDirectory: true)
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("app.log")
    }()

    static var fileURL: URL { logURL }

    private static let stamp: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f
    }()

    static func write(_ items: Any...) {
        let text = items.map { "\($0)" }.joined(separator: " ")
        let line = stamp.string(from: Date()) + " " + text + "\n"
        guard let data = line.data(using: .utf8) else { return }

        lock.lock()
        defer { lock.unlock() }
        rotateIfNeeded()
        if let handle = try? FileHandle(forWritingTo: logURL) {
            defer { try? handle.close() }
            handle.seekToEndOfFile()
            handle.write(data)
        } else {
            try? data.write(to: logURL, options: .atomic)
        }
    }

    private static func rotateIfNeeded() {
        let fm = FileManager.default
        guard let attrs = try? fm.attributesOfItem(atPath: logURL.path),
              let size = attrs[.size] as? Int, size > maxBytes else { return }
        let backup = logURL.deletingLastPathComponent().appendingPathComponent("app.log.1")
        try? fm.removeItem(at: backup)
        try? fm.moveItem(at: logURL, to: backup)
    }

    /// 在 Finder 里定位日志文件
    static func reveal() {
        try? FileManager.default.createDirectory(
            at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logURL.path) {
            write("created by reveal")
        }
        NSWorkspace.shared.activateFileViewerSelecting([logURL])
    }
}
