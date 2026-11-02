import Foundation
import IOKit

// MARK: - CPU 温度

/// Apple Silicon 的 CPU 温度。
///
/// macOS 没有公开 API，只能走 IOHIDEventSystemClient 的传感器服务
/// （Stats、Hot 这些工具用的都是这条路径）。属私有 API：自签分发没问题，
/// App Store 不收。符号用 dlsym 动态取，避免编译期链接私有接口。
///
/// 两个在这台 macOS 26 / M2 上实测出来的坑：
///
/// 1. **不要设 matching**。教科书写法是先匹配
///    `PrimaryUsagePage 0xff00 / PrimaryUsage 5` 再 CopyServices，
///    在 macOS 26 上一个服务都拿不到；不匹配取全量（133 个）、再按名字筛才有效。
/// 2. 传感器名是 `PMU tdie1`…`PMU tdie8` 和 `PMU2 tdie1`…`PMU2 tdie8`
///    （性能核 / 能效核各一组，共 16 个）。取平均值比取某一个核心稳，
///    不会因为某个核空闲就跳一下。`PMU tcal` / `tdev*` 是校准和设备温度，不取。
///
/// 读不到（Intel 机型、系统收紧）就返回 nil，界面上这一项直接不显示。
final class CPUTemp {
    static let shared = CPUTemp()

    private typealias CreateFn = @convention(c) (CFAllocator?) -> CFTypeRef?
    private typealias CopyServicesFn = @convention(c) (CFTypeRef) -> CFArray?
    private typealias CopyPropertyFn = @convention(c) (CFTypeRef, CFString) -> CFTypeRef?
    private typealias CopyEventFn = @convention(c) (CFTypeRef, Int64, Int32, Int64) -> CFTypeRef?
    private typealias FloatFn = @convention(c) (CFTypeRef, Int32) -> Double

    /// kIOHIDEventTypeTemperature
    private static let tempEvent: Int64 = 15
    /// IOHIDEventFieldBase(kIOHIDEventTypeTemperature)
    private static let tempField: Int32 = 15 << 16

    private var client: CFTypeRef?
    private var copyServices: CopyServicesFn?
    private var copyProperty: CopyPropertyFn?
    private var copyEvent: CopyEventFn?
    private var getFloat: FloatFn?
    private var didSetup = false
    private var loggedUnavailable = false
    private var loggedFirstRead = false

    /// 最近一次读到的核心传感器个数，面板文案里用来显示「平均 N 个核心」
    private(set) var coreCount = 0

    private init() {}

    /// 首次调用时加载符号并建 client；失败只记一条日志，不反复刷
    private func setupIfNeeded() {
        guard !didSetup else { return }
        didSetup = true

        guard let handle = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_NOW) else {
            Log.write("[温度] 加载 IOKit 失败，CPU 温度不可用")
            return
        }
        func sym<T>(_ name: String) -> T? {
            guard let p = dlsym(handle, name) else { return nil }
            return unsafeBitCast(p, to: T.self)
        }

        guard let create: CreateFn = sym("IOHIDEventSystemClientCreate"),
              let services: CopyServicesFn = sym("IOHIDEventSystemClientCopyServices"),
              let property: CopyPropertyFn = sym("IOHIDServiceClientCopyProperty"),
              let event: CopyEventFn = sym("IOHIDServiceClientCopyEvent"),
              let value: FloatFn = sym("IOHIDEventGetFloatValue"),
              let created = create(kCFAllocatorDefault) else {
            Log.write("[温度] IOHID 符号缺失，CPU 温度不可用")
            return
        }

        copyServices = services
        copyProperty = property
        copyEvent = event
        getFloat = value
        client = created
    }

    /// 传感器是不是 CPU 核心温度
    private func isCPUSensor(_ name: String) -> Bool {
        let low = name.lowercased()
        return low.contains("tdie") || low.contains("cpu")
    }

    /// CPU 平均温度（°C）。正常返回 20…110 之间的数，读不到返回 nil。
    func read() -> Double? {
        setupIfNeeded()

        guard let client = client,
              let copyServices = copyServices,
              let copyProperty = copyProperty,
              let copyEvent = copyEvent,
              let getFloat = getFloat else {
            if !loggedUnavailable {
                loggedUnavailable = true
                Log.write("[温度] 数据源不可用，菜单栏不显示温度")
            }
            return nil
        }

        // 注意：这里刻意不调 SetMatching，见文件头的说明
        guard let services = copyServices(client) as? [CFTypeRef] else { return nil }

        var sum = 0.0
        var count = 0
        for service in services {
            guard let raw = copyProperty(service, "Product" as CFString) as? String,
                  isCPUSensor(raw) else { continue }
            guard let event = copyEvent(service, CPUTemp.tempEvent, 0, 0) else { continue }
            let celsius = getFloat(event, CPUTemp.tempField)
            // 过滤掉 0 / 异常值（传感器未就绪时会读到 0）
            if celsius > 1 && celsius < 150 {
                sum += celsius
                count += 1
            }
        }

        guard count > 0 else { return nil }
        coreCount = count
        let avg = sum / Double(count)
        if !loggedFirstRead {
            loggedFirstRead = true
            Log.write(String(format: "[温度] 读到 %d 个核心传感器，平均 %.1f°C", count, avg))
        }
        return avg
    }
}

/// 菜单栏 / 面板共用的写法：整数 + 度
func fmtTemp(_ celsius: Double?) -> String {
    guard let celsius = celsius else { return "—" }
    return String(format: "%.0f°", celsius)
}
