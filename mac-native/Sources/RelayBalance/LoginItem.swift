import AppKit
import Foundation

#if canImport(ServiceManagement)
import ServiceManagement
#endif

/// 开机自启（准确说是「登录时打开」）。
///
/// 走 SMAppService.mainApp，而不是自己往 ~/Library/LaunchAgents 写 plist：
/// 系统会把这一项归到「设置 → 通用 → 登录项与扩展」，用户能在同一个地方
/// 看到、也能统一关掉；手写 plist 不会出现在那儿，卸载时还容易留残留。
///
/// 代价是 SMAppService 需要 macOS 13，而这个包最低支持 12，
/// 所以 12 上 `isSupported` 为 false，界面上这个开关直接不显示。
///
/// 另一点：本项目的 .app 是 ad-hoc 签名，每次重打包 cdhash 都会变，
/// 系统有可能把旧登记判为无效并在登录项里标灰，重装后重新勾一次即可。
enum LoginItem {
    static var isSupported: Bool {
        if #available(macOS 13.0, *) { return true }
        return false
    }

    static var isEnabled: Bool {
        guard #available(macOS 13.0, *) else { return false }
        return SMAppService.mainApp.status == .enabled
    }

    /// 已经登记，但用户还没在系统设置里允许（macOS 会要求确认一次）
    static var needsApproval: Bool {
        guard #available(macOS 13.0, *) else { return false }
        return SMAppService.mainApp.status == .requiresApproval
    }

    /// 返回 nil 表示成功，否则是给用户看的一句话说明
    static func set(_ on: Bool) -> String? {
        guard #available(macOS 13.0, *) else { return "当前系统不支持（需要 macOS 13 或更高）" }
        let service = SMAppService.mainApp
        do {
            if on {
                // 已经是 enabled 还重复 register 会抛错，先挡一下
                if service.status != .enabled { try service.register() }
            } else if service.status != .notRegistered {
                try service.unregister()
            }
            Log.write("[自启] \(on ? "已登记" : "已取消")，当前状态 \(describe(service.status))")
            return nil
        } catch {
            Log.write("[自启] \(on ? "登记" : "取消")失败：\(error.localizedDescription)")
            return error.localizedDescription
        }
    }

    /// 状态是 requiresApproval 时把用户送到系统设置的登录项页
    static func openSystemSettings() {
        guard #available(macOS 13.0, *) else { return }
        SMAppService.openSystemSettingsLoginItems()
    }

    @available(macOS 13.0, *)
    private static func describe(_ status: SMAppService.Status) -> String {
        switch status {
        case .enabled: return "enabled"
        case .notRegistered: return "notRegistered"
        case .notFound: return "notFound"
        case .requiresApproval: return "requiresApproval"
        @unknown default: return "unknown(\(status.rawValue))"
        }
    }
}
