import Foundation
import SQLite3

// MARK: - 聚合

struct ModelAgg {
    var req = 0
    var tin = 0
    var tout = 0
    var cache = 0

    var total: Int { tin + tout }
}

struct DayAgg {
    var req = 0
    var tin = 0
    var tout = 0
    var cache = 0
    var src = ""
    var models: [String: ModelAgg] = [:]

    var total: Int { tin + tout }

    mutating func add(model: String?, tin: Int, tout: Int, cache: Int) {
        let name = (model?.isEmpty ?? true) ? "unknown" : model!
        var m = models[name] ?? ModelAgg()
        m.req += 1
        m.tin += tin
        m.tout += tout
        m.cache += cache
        models[name] = m
    }

    mutating func merge(_ other: DayAgg) {
        req += other.req
        tin += other.tin
        tout += other.tout
        cache += other.cache
        if src.isEmpty { src = other.src }
        for (name, v) in other.models {
            var m = models[name] ?? ModelAgg()
            m.req += v.req
            m.tin += v.tin
            m.tout += v.tout
            m.cache += v.cache
            models[name] = m
        }
    }
}

private func intOf(_ v: Any?) -> Int {
    if let n = v as? NSNumber { return n.intValue }
    if let s = v as? String { return Int(s) ?? 0 }
    return 0
}

// MARK: - 日期

enum Day {
    static let fmt: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    static func str(_ d: Date = Date()) -> String { fmt.string(from: d) }

    static func today() -> String { str() }

    static func weekDates(_ ref: Date = Date()) -> [String] {
        var cal = Calendar.current
        cal.firstWeekday = 2
        let start = cal.dateInterval(of: .weekOfYear, for: ref)?.start ?? ref
        var out: [String] = []
        var cur = start
        while cur <= ref {
            out.append(str(cur))
            guard let next = cal.date(byAdding: .day, value: 1, to: cur) else { break }
            cur = next
        }
        return out
    }

    static func monthDates(_ ref: Date = Date()) -> [String] {
        let cal = Calendar.current
        let comps = cal.dateComponents([.year, .month], from: ref)
        guard let first = cal.date(from: comps) else { return [] }
        var out: [String] = []
        var cur = first
        while cur <= ref {
            out.append(str(cur))
            guard let next = cal.date(byAdding: .day, value: 1, to: cur) else { break }
            cur = next
        }
        return out
    }

    static func recentDates(_ n: Int, _ ref: Date = Date()) -> [String] {
        let cal = Calendar.current
        var out: [String] = []
        for i in stride(from: n - 1, through: 0, by: -1) {
            if let d = cal.date(byAdding: .day, value: -i, to: ref) { out.append(str(d)) }
        }
        return out
    }
}

// MARK: - 读取器

final class UsageReader {
    static let shared = UsageReader()

    private var fileCache: [String: (stamp: String, agg: DayAgg)] = [:]
    private var tail: [String: DayAgg] = [:]
    private var tailOffset: [String: UInt64] = [:]
    private var ccToday: (t: TimeInterval, agg: DayAgg?)?

    // ---- magpie

    private func parseMagpie(_ url: URL) -> DayAgg {
        var agg = DayAgg()
        agg.src = "magpie"
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return agg }
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            guard let data = line.data(using: .utf8),
                  let raw = try? JSONSerialization.jsonObject(with: data),
                  let obj = raw as? [String: Any] else { continue }
            if (obj["done"] as? Bool) != true { continue }
            guard let usages = obj["usage"] as? [[String: Any]] else { continue }
            for u in usages {
                let tin = intOf(u["in"])
                let tout = intOf(u["out"])
                let cache = intOf(u["cache_read"])
                if tin == 0 && tout == 0 { continue }
                agg.req += 1
                agg.tin += tin
                agg.tout += tout
                agg.cache += cache
                agg.add(model: u["model"] as? String, tin: tin, tout: tout, cache: cache)
            }
        }
        return agg
    }

    func scanMagpie(_ days: Int = 40) -> [String: DayAgg] {
        var out: [String: DayAgg] = [:]
        let fm = FileManager.default
        guard let files = try? fm.contentsOfDirectory(at: Paths.magpieRouting,
                                                      includingPropertiesForKeys: [.fileSizeKey],
                                                      options: [.skipsHiddenFiles]) else {
            return out
        }
        let jsonl = files.filter { $0.pathExtension == "jsonl" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
            .suffix(days)
        for url in jsonl {
            let name = url.lastPathComponent
            let date = name.hasSuffix(".jsonl") ? String(name.dropLast(6)) : name
            let attrs = try? fm.attributesOfItem(atPath: url.path)
            let size = (attrs?[.size] as? NSNumber)?.intValue ?? 0
            let mtime = (attrs?[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
            let stamp = "\(Int(mtime))-\(size)"

            let agg: DayAgg
            if let cached = fileCache[url.path], cached.stamp == stamp {
                agg = cached.agg
            } else {
                agg = parseMagpie(url)
                fileCache[url.path] = (stamp, agg)
            }
            if agg.req > 0 { out[date] = agg }
        }
        return out
    }

    // ---- CC Switch

    private func openDB() -> OpaquePointer? {
        guard FileManager.default.fileExists(atPath: Paths.ccswitchDB.path) else { return nil }
        var db: OpaquePointer?
        let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_URI
        if sqlite3_open_v2("file:\(Paths.ccswitchDB.path)?mode=ro", &db, flags, nil) != SQLITE_OK {
            return nil
        }
        return db
    }

    private func text(_ stmt: OpaquePointer?, _ i: Int32) -> String {
        guard let c = sqlite3_column_text(stmt, i) else { return "" }
        return String(cString: c)
    }

    func scanCCSwitch(_ days: Int = 400) -> [String: DayAgg] {
        var out: [String: DayAgg] = [:]
        guard let db = openDB() else { return out }
        defer { sqlite3_close(db) }

        let cal = Calendar.current
        let since = cal.date(byAdding: .day, value: -days, to: Date()) ?? Date()
        let sinceStr = Day.str(since)

        // 1) 逐请求明细
        let sql1 = """
            select created_at, model, input_tokens, output_tokens, cache_read_tokens
            from proxy_request_logs where created_at is not null
            """
        var stmt: OpaquePointer?
        if sqlite3_prepare_v2(db, sql1, -1, &stmt, nil) == SQLITE_OK {
            while sqlite3_step(stmt) == SQLITE_ROW {
                let ts = sqlite3_column_double(stmt, 0)
                let model = text(stmt, 1)
                let tin = Int(sqlite3_column_int64(stmt, 2))
                let tout = Int(sqlite3_column_int64(stmt, 3))
                let cache = Int(sqlite3_column_int64(stmt, 4))
                if tin == 0 && tout == 0 { continue }
                let date = Day.str(Date(timeIntervalSince1970: ts))
                if date < sinceStr { continue }
                var agg = out[date] ?? DayAgg()
                agg.src = "ccswitch"
                agg.req += 1
                agg.tin += tin
                agg.tout += tout
                agg.cache += cache
                agg.add(model: model, tin: tin, tout: tout, cache: cache)
                out[date] = agg
            }
        }
        sqlite3_finalize(stmt)

        // 2) 按天汇总（补明细表没有的更早数据）
        let sql2 = """
            select date, model, sum(request_count), sum(input_tokens),
                   sum(output_tokens), sum(cache_read_tokens)
            from usage_daily_rollups where date >= ? group by date, model
            """
        var stmt2: OpaquePointer?
        if sqlite3_prepare_v2(db, sql2, -1, &stmt2, nil) == SQLITE_OK {
            sqlite3_bind_text(stmt2, 1, sinceStr, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
            while sqlite3_step(stmt2) == SQLITE_ROW {
                let date = text(stmt2, 0)
                if date.isEmpty { continue }
                if out[date] != nil { continue }   // 当天已有明细，汇总表会重复
                let model = text(stmt2, 1)
                let req = Int(sqlite3_column_int64(stmt2, 2))
                let tin = Int(sqlite3_column_int64(stmt2, 3))
                let tout = Int(sqlite3_column_int64(stmt2, 4))
                let cache = Int(sqlite3_column_int64(stmt2, 5))
                var agg = DayAgg()
                agg.src = "ccswitch"
                agg.req += req
                agg.tin += tin
                agg.tout += tout
                agg.cache += cache
                agg.add(model: model, tin: tin, tout: tout, cache: cache)
                out[date] = agg
            }
        }
        sqlite3_finalize(stmt2)
        return out
    }

    // ---- 合并

    func collect(_ days: Int = 120) -> [String: DayAgg] {
        var merged = scanCCSwitch(days)
        for (date, agg) in scanMagpie(days) { merged[date] = agg }   // magpie 优先
        return merged
    }

    func totals(_ data: [String: DayAgg], dates: [String]) -> DayAgg {
        var agg = DayAgg()
        for d in dates {
            guard let row = data[d] else { continue }
            agg.merge(row)
        }
        return agg
    }

    func byMonth(_ data: [String: DayAgg]) -> [(String, DayAgg)] {
        var out: [String: DayAgg] = [:]
        for (date, row) in data {
            let key = String(date.prefix(7))
            var agg = out[key] ?? DayAgg()
            agg.merge(row)
            out[key] = agg
        }
        return out.sorted { $0.key > $1.key }
    }

    // ---- 今日实时（面板 / 菜单栏用，5 秒一轮）

    func todayLive() -> DayAgg {
        let date = Day.today()
        let url = Paths.magpieRouting.appendingPathComponent("\(date).jsonl")
        consumeNew(url)

        if let agg = tail[url.path], agg.req > 0 { return agg }

        let now = Date().timeIntervalSince1970
        if ccToday == nil || now - ccToday!.t > 60 {
            ccToday = (now, scanCCSwitch(2)[date])
        }
        if let row = ccToday?.agg { return row }
        return DayAgg()
    }

    /// 只读文件新增部分：5 秒一轮，不能每次把整天重读
    private func consumeNew(_ url: URL) {
        let fm = FileManager.default
        guard let attrs = try? fm.attributesOfItem(atPath: url.path),
              let size = (attrs[.size] as? NSNumber)?.uint64Value else { return }

        var offset = tailOffset[url.path] ?? 0
        if size < offset {           // 文件被截断或换天
            offset = 0
            tail[url.path] = DayAgg()
        }
        if size <= offset { return }

        guard let handle = try? FileHandle(forReadingFrom: url) else { return }
        defer { try? handle.close() }
        do {
            try handle.seek(toOffset: offset)
            guard let chunk = try handle.readToEnd(), !chunk.isEmpty else { return }

            var agg = tail[url.path] ?? DayAgg()
            agg.src = "magpie"
            var pos = 0
            let bytes = [UInt8](chunk)
            while pos < bytes.count {
                guard let nl = bytes[pos...].firstIndex(of: 0x0A) else { break }
                let lineBytes = Array(bytes[pos..<nl])
                pos = nl + 1
                guard !lineBytes.isEmpty,
                      let raw = try? JSONSerialization.jsonObject(with: Data(lineBytes)),
                      let obj = raw as? [String: Any] else { continue }
                if (obj["done"] as? Bool) != true { continue }
                guard let usages = obj["usage"] as? [[String: Any]] else { continue }
                for u in usages {
                    let tin = intOf(u["in"])
                    let tout = intOf(u["out"])
                    if tin == 0 && tout == 0 { continue }
                    agg.req += 1
                    agg.tin += tin
                    agg.tout += tout
                    agg.cache += intOf(u["cache_read"])
                }
            }
            tail[url.path] = agg
            tailOffset[url.path] = offset + UInt64(pos)
        } catch {
            return
        }
    }
}
