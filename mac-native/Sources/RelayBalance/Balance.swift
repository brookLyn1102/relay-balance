import Foundation

// MARK: - 余额

struct Balance {
    var total: Double
    var used: Double
    var left: Double
    var at: Date
    var source: String
}

struct WeekStat {
    var spent: Double
    var quota: Double
    var pct: Double
    var pool: Double
}

enum BalanceError: LocalizedError {
    case badURL
    case message(String)

    var errorDescription: String? {
        switch self {
        case .badURL: return "地址不对"
        case .message(let m): return m
        }
    }
}

private final class Box<T> {
    var value: T?
}

func apiGet(base: String, key: String, path: String) throws -> [String: Any] {
    guard let url = URL(string: base + path) else { throw BalanceError.badURL }
    var req = URLRequest(url: url)
    req.timeoutInterval = 20
    req.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
    req.setValue("application/json", forHTTPHeaderField: "Accept")

    let sem = DispatchSemaphore(value: 0)
    let payload = Box<[String: Any]>()
    let failure = Box<Error>()

    URLSession.shared.dataTask(with: req) { data, response, error in
        defer { sem.signal() }
        if let error = error {
            failure.value = error
            return
        }
        guard let data = data else {
            failure.value = BalanceError.message("没有返回内容")
            return
        }
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            let body = String(data: data, encoding: .utf8) ?? ""
            let brief = body.replacingOccurrences(of: "\n", with: " ").prefix(48)
            failure.value = BalanceError.message("HTTP \(http.statusCode) \(brief)")
            return
        }
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            payload.value = obj
        } else {
            failure.value = BalanceError.message("返回不是 JSON")
        }
    }.resume()

    _ = sem.wait(timeout: .now() + 25)
    if let error = failure.value { throw error }
    guard let obj = payload.value else { throw BalanceError.message("查询超时") }
    return obj
}

func queryBalance(base: String, key: String) throws -> Balance {
    let sub = try apiGet(base: base, key: key, path: "/v1/dashboard/billing/subscription")
    let use = try apiGet(base: base, key: key, path: "/v1/dashboard/billing/usage")
    let total = (sub["hard_limit_usd"] as? NSNumber)?.doubleValue ?? 0
    let used = ((use["total_usage"] as? NSNumber)?.doubleValue ?? 0) / 100.0
    return Balance(total: total, used: used, left: total - used, at: Date(), source: "")
}

func fetchBalance(_ cfg: Config) throws -> Balance {
    var base: String
    var key: String
    var source: String

    if let acc = activeAccount(cfg) {
        base = normBase(acc.base)
        key = acc.key
        source = acc.name
    } else {
        let pair = try magpieCreds(providerID: cfg.provider)
        base = pair.0
        key = pair.1
        source = "magpie · \(cfg.provider ?? "默认")"
    }

    var data = try queryBalance(base: base, key: key)
    data.source = source
    return data
}

func weekSplit(_ cfg: Config, pool: Double) -> WeekStat {
    let quota = cfg.weekQuota > 0 ? cfg.weekQuota : 100
    let spent = max(0, quota - pool)
    let pct = quota > 0 ? spent / quota * 100 : 0
    return WeekStat(spent: spent, quota: quota, pct: pct, pool: pool)
}

// MARK: - 格式化

func fmtMoney(_ v: Double) -> String {
    let body = String(format: "$%.2f", abs(v))
    // 加千分位
    let parts = String(body.dropFirst()).components(separatedBy: ".")
    let intPart = parts[0]
    let dec = parts.count > 1 ? parts[1] : "00"
    var out = ""
    for (i, ch) in intPart.reversed().enumerated() {
        if i > 0 && i % 3 == 0 { out.append(",") }
        out.append(ch)
    }
    let grouped = String(out.reversed())
    let s = "$\(grouped).\(dec)"
    return v < 0 ? "-" + s : s
}

func fmtSpeed(_ v: Double) -> String {
    if v < 1024 { return String(format: "%.0f B/s", v) }
    if v < 1024 * 1024 { return String(format: "%.1f KB/s", v / 1024) }
    return String(format: "%.2f MB/s", v / 1024 / 1024)
}

func fmtSpeedShort(_ v: Double) -> String {
    if v < 1024 { return String(format: "%.0fB", v) }
    if v < 1024 * 1024 { return String(format: "%.0fK", v / 1024) }
    return String(format: "%.1fM", v / 1024 / 1024)
}

func fmtTokens(_ n: Int) -> String {
    if n >= 1_000_000_000 { return String(format: "%.2fB", Double(n) / 1_000_000_000) }
    if n >= 1_000_000 { return String(format: "%.2fM", Double(n) / 1_000_000) }
    if n >= 10_000 { return String(format: "%.0fK", Double(n) / 1_000) }
    return "\(n)"
}

func fmtMem(_ gb: Double?) -> String {
    guard let gb = gb else { return "—" }
    if gb < 1 { return String(format: "%.0fM", gb * 1024) }
    if gb >= 10 { return String(format: "%.0fG", gb) }
    return String(format: "%.1fG", gb)
}
