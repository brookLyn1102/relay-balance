import Foundation
import Darwin

// MARK: - 内存

struct MemInfo {
    var availableGB: Double
    var usedPct: Double
}

func readMem() -> MemInfo? {
    var stats = vm_statistics64_data_t()
    var count = mach_msg_type_number_t(
        MemoryLayout<vm_statistics64_data_t>.size / MemoryLayout<integer_t>.size)

    let kr = withUnsafeMutablePointer(to: &stats) { ptr -> kern_return_t in
        ptr.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { intPtr in
            host_statistics64(mach_host_self(), HOST_VM_INFO64, intPtr, &count)
        }
    }
    guard kr == KERN_SUCCESS else { return nil }

    var pageSize: vm_size_t = 0
    host_page_size(mach_host_self(), &pageSize)
    let page = Double(pageSize)

    // macOS 的「可用」包含可回收的 inactive / speculative / purgeable
    let free = Double(stats.free_count) * page
    let inactive = Double(stats.inactive_count) * page
    let speculative = Double(stats.speculative_count) * page
    let purgeable = Double(stats.purgeable_count) * page
    let available = free + inactive + speculative + purgeable

    let total = Double(ProcessInfo.processInfo.physicalMemory)
    let pct = total > 0 ? (1 - available / total) * 100 : 0
    return MemInfo(availableGB: available / 1_073_741_824, usedPct: pct)
}

// MARK: - 网速

final class NetMeter {
    private var last: (sent: UInt64, recv: UInt64, t: TimeInterval)?
    private(set) var up: Double = 0
    private(set) var down: Double = 0

    /// 回环与虚拟接口要排掉：本机代理会让同一份流量算两遍
    private static let skipPrefix = ["lo", "utun", "awdl", "llw", "gif",
                                     "stf", "bridge", "ap", "anpi"]

    private func counters() -> (UInt64, UInt64) {
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0, let first = ifaddr else { return (0, 0) }
        defer { freeifaddrs(ifaddr) }

        var sent: UInt64 = 0
        var recv: UInt64 = 0
        var ptr = first

        while true {
            let item = ptr.pointee
            if let addr = item.ifa_addr,
               addr.pointee.sa_family == UInt8(AF_LINK),
               let raw = item.ifa_data {
                let name = String(cString: item.ifa_name).lowercased()
                let skipped = NetMeter.skipPrefix.contains { name.hasPrefix($0) }
                    || name.contains("loopback")
                    || name.contains("teredo")
                if !skipped {
                    let data = raw.assumingMemoryBound(to: if_data.self).pointee
                    recv += UInt64(data.ifi_ibytes)
                    sent += UInt64(data.ifi_obytes)
                }
            }
            guard let next = item.ifa_next else { break }
            ptr = next
        }
        return (sent, recv)
    }

    func sample() {
        let (sent, recv) = counters()
        let now = ProcessInfo.processInfo.systemUptime
        guard let prev = last else {
            last = (sent, recv, now)
            return
        }
        let dt = max(0.2, now - prev.t)
        last = (sent, recv, now)
        // 计数器是累加的，用 &- 防回绕
        up = max(0, Double(sent &- prev.sent) / dt)
        down = max(0, Double(recv &- prev.recv) / dt)
    }
}
