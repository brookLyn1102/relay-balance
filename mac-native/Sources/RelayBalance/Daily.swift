import Foundation

/// 按天记录「已用额度」基准，用来算今天花了多少。
///
/// 口径与 Windows 版 rb_daily.py 完全一致：
/// 今日用量 = 当前已用 − 当天 0 点的基准。
/// 基准在每天第一次查询时落下；跨天那一刻 Model 会主动查一次，
/// 半夜休眠起来后也靠「日期变了」补一次。
enum DailyStore {
    static let keepDays = 120

    private struct Last: Codable {
        var ts: Double
        var used: Double
    }

    private struct Stored: Codable {
        var days: [String: Double]
        var last: Last?
    }

    static let path = Paths.configDir.appendingPathComponent("daily.json")

    private static let df: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone.current
        return f
    }()

    static func todayKey(_ now: Date = Date()) -> String { df.string(from: now) }

    private static func load() -> Stored {
        guard let data = try? Data(contentsOf: path),
              let s = try? JSONDecoder().decode(Stored.self, from: data) else {
            return Stored(days: [:], last: nil)
        }
        return s
    }

    private static func save(_ s: Stored) {
        try? FileManager.default.createDirectory(at: Paths.configDir,
                                                 withIntermediateDirectories: true)
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted]
        if let data = try? enc.encode(s) {
            try? data.write(to: path, options: .atomic)
        }
    }

    /// 记下今日基准（当天只写一次），返回基准值。
    @discardableResult
    static func mark(_ used: Double, now: Date = Date()) -> Double {
        let today = todayKey(now)
        var s = load()
        var base = s.days[today]
        if base == nil {
            base = used
            if let last = s.last {
                let lastDay = df.string(from: Date(timeIntervalSince1970: last.ts))
                let yday = df.string(from: now.addingTimeInterval(-86_400))
                // 前一天看到过数字，说明只是中途关机 / 休眠：拿它当起点
                if lastDay == yday && last.used <= used { base = last.used }
            }
            s.days[today] = base
            if s.days.count > keepDays {
                for k in s.days.keys.sorted().prefix(s.days.count - keepDays) {
                    s.days.removeValue(forKey: k)
                }
            }
            Log.write(String(format: "daily 基准 %@ = %.2f", today, base ?? used))
        }
        s.last = Last(ts: now.timeIntervalSince1970, used: used)
        save(s)
        return base ?? used
    }

    /// 今天花掉多少（含刚才这一次查询的增量）。
    static func usedToday(_ used: Double, now: Date = Date()) -> Double {
        max(0, used - mark(used, now: now))
    }

    static func base(of date: String? = nil) -> Double {
        load().days[date ?? todayKey()] ?? 0
    }

    /// 本周一（ISO 周，周一算第一天）的日期
    static func weekStart(_ now: Date = Date()) -> String {
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = .current
        let comps = cal.dateComponents([.yearForWeekOfYear, .weekOfYear], from: now)
        if let monday = cal.date(from: comps) { return df.string(from: monday) }
        return todayKey(now)
    }

    /// 本周一 0 点以来花了多少。
    /// 周一的基准本来就在 days 里（那天的 0 点快照），不用另存；
    /// 周一没记录就返回 nil，调用方退回「周额度 − 池子」的估算。
    static func usedThisWeek(_ used: Double, now: Date = Date()) -> Double? {
        guard let base = load().days[weekStart(now)] else { return nil }
        return max(0, used - base)
    }
}
