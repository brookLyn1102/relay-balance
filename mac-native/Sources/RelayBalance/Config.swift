import Foundation
import SQLite3

// MARK: - 路径

enum Paths {
    static let home = FileManager.default.homeDirectoryForCurrentUser
    static let configDir = home.appendingPathComponent(".config/relay-balance")
    static let config = configDir.appendingPathComponent("config.json")
    static let magpieProviders = home.appendingPathComponent(".config/magpie/providers.json")
    static let magpieRouting = home.appendingPathComponent(".config/magpie/routing")
    static let ccswitchDB = home.appendingPathComponent(".cc-switch/cc-switch.db")
}

let APP_NAME = "中转管家"

// MARK: - 账号 / 配置

struct Account: Codable, Identifiable, Hashable {
    var name: String
    var base: String
    var key: String

    var id: String { name }

    init(name: String = "", base: String = "", key: String = "") {
        self.name = name
        self.base = base
        self.key = key
    }
}

struct Config: Codable {
    var provider: String?
    var interval: Int = 600
    var nightPause: Bool = true
    var nightFrom: Double = 21
    var nightTo: Double = 8.5
    var topmost: Bool = true
    var panelX: Double?
    var panelY: Double?
    var accounts: [Account] = []
    var active: String?
    var weekQuota: Double = 100
    var weekKey: String?
    var netPanel: Bool = true
    var menuSpeed: Bool = true
    var menuBalance: Bool = true
    var panelToken: Bool = true
    var menuToken: Bool = true
    var menuMem: Bool = true

    enum CodingKeys: String, CodingKey {
        case provider, interval, accounts, active, topmost
        case nightPause = "night_pause"
        case nightFrom = "night_from"
        case nightTo = "night_to"
        case panelX = "panel_x"
        case panelY = "panel_y"
        case weekQuota = "week_quota"
        case weekKey = "week_key"
        case netPanel = "net_panel"
        case menuSpeed = "menu_speed"
        case menuBalance = "menu_balance"
        case panelToken = "panel_token"
        case menuToken = "menu_token"
        case menuMem = "menu_mem"
    }

    init() {}

    /// 老配置文件缺字段时用默认值兜底，不整体失败。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        provider = try c.decodeIfPresent(String.self, forKey: .provider)
        interval = try c.decodeIfPresent(Int.self, forKey: .interval) ?? 600
        nightPause = try c.decodeIfPresent(Bool.self, forKey: .nightPause) ?? true
        nightFrom = try c.decodeIfPresent(Double.self, forKey: .nightFrom) ?? 21
        nightTo = try c.decodeIfPresent(Double.self, forKey: .nightTo) ?? 8.5
        topmost = try c.decodeIfPresent(Bool.self, forKey: .topmost) ?? true
        panelX = try c.decodeIfPresent(Double.self, forKey: .panelX)
        panelY = try c.decodeIfPresent(Double.self, forKey: .panelY)
        accounts = try c.decodeIfPresent([Account].self, forKey: .accounts) ?? []
        active = try c.decodeIfPresent(String.self, forKey: .active)
        weekQuota = try c.decodeIfPresent(Double.self, forKey: .weekQuota) ?? 100
        weekKey = try c.decodeIfPresent(String.self, forKey: .weekKey)
        netPanel = try c.decodeIfPresent(Bool.self, forKey: .netPanel) ?? true
        menuSpeed = try c.decodeIfPresent(Bool.self, forKey: .menuSpeed) ?? true
        menuBalance = try c.decodeIfPresent(Bool.self, forKey: .menuBalance) ?? true
        panelToken = try c.decodeIfPresent(Bool.self, forKey: .panelToken) ?? true
        menuToken = try c.decodeIfPresent(Bool.self, forKey: .menuToken) ?? true
        menuMem = try c.decodeIfPresent(Bool.self, forKey: .menuMem) ?? true
    }
}

enum ConfigStore {
    static func load() -> Config {
        try? FileManager.default.createDirectory(at: Paths.configDir,
                                                 withIntermediateDirectories: true)
        guard let data = try? Data(contentsOf: Paths.config) else { return Config() }
        return (try? JSONDecoder().decode(Config.self, from: data)) ?? Config()
    }

    static func save(_ cfg: Config) {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes, .sortedKeys]
        guard let data = try? enc.encode(cfg) else { return }
        try? FileManager.default.createDirectory(at: Paths.configDir,
                                                 withIntermediateDirectories: true)
        try? data.write(to: Paths.config, options: .atomic)
    }
}

// MARK: - 时间窗

func fmtHM(_ v: Double) -> String {
    var h = Int(v)
    var m = Int(((v - Double(h)) * 60).rounded())
    if m >= 60 { h += 1; m = 0 }
    return String(format: "%02d:%02d", h, m)
}

func inNight(_ cfg: Config, now: Date = Date()) -> Bool {
    guard cfg.nightPause else { return false }
    if cfg.nightFrom == cfg.nightTo { return false }
    let c = Calendar.current.dateComponents([.hour, .minute], from: now)
    let t = Double(c.hour ?? 0) + Double(c.minute ?? 0) / 60.0
    if cfg.nightFrom > cfg.nightTo {
        return t >= cfg.nightFrom || t < cfg.nightTo
    }
    return t >= cfg.nightFrom && t < cfg.nightTo
}

func weekKey(_ date: Date = Date()) -> String {
    var cal = Calendar(identifier: .iso8601)
    cal.firstWeekday = 2
    let week = cal.component(.weekOfYear, from: date)
    let year = cal.component(.yearForWeekOfYear, from: date)
    return String(format: "%04d-W%02d", year, week)
}

// MARK: - 账号解析

func normBase(_ base: String) -> String {
    var s = base.trimmingCharacters(in: .whitespacesAndNewlines)
    while s.hasSuffix("/") { s.removeLast() }
    if s.hasSuffix("/v1") { s.removeLast(3) }
    return s
}

func urlHost(_ base: String) -> String {
    let body = base.components(separatedBy: "//").last ?? base
    let host = body.components(separatedBy: "/").first ?? base
    return host.isEmpty ? base : host
}

func activeAccount(_ cfg: Config) -> Account? {
    guard let name = cfg.active else { return nil }
    return cfg.accounts.first { $0.name == name && !$0.base.isEmpty && !$0.key.isEmpty }
}

/// magpie 的 providers.json -> 递归找同时带 url 与 sk- key 的节点
func magpieCreds(providerID: String?) throws -> (String, String) {
    guard let data = try? Data(contentsOf: Paths.magpieProviders),
          let root = try? JSONSerialization.jsonObject(with: data) else {
        throw NSError(domain: "relay", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "读不到 magpie 配置"])
    }
    if let pid = providerID, !pid.isEmpty {
        if let node = findNode(root, id: pid), let pair = pairIn(node) {
            return (normBase(pair.0), pair.1)
        }
        throw NSError(domain: "relay", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "magpie 里没有供应商 \(pid)"])
    }
    if let pair = firstPair(root) {
        return (normBase(pair.0), pair.1)
    }
    throw NSError(domain: "relay", code: 3,
                  userInfo: [NSLocalizedDescriptionKey: "magpie 里没有可用的供应商"])
}

private func findNode(_ node: Any, id: String) -> Any? {
    if let dict = node as? [String: Any] {
        if let v = dict["id"] as? String, v == id { return dict }
        for value in dict.values {
            if let hit = findNode(value, id: id) { return hit }
        }
    } else if let arr = node as? [Any] {
        for item in arr {
            if let hit = findNode(item, id: id) { return hit }
        }
    }
    return nil
}

private func pairIn(_ node: Any) -> (String, String)? {
    guard let dict = node as? [String: Any] else { return nil }
    var base: String?
    var key: String?
    for value in dict.values {
        guard let s = value as? String else { continue }
        if base == nil && s.hasPrefix("http") { base = s }
        if key == nil && s.hasPrefix("sk-") { key = s }
    }
    if let b = base, let k = key { return (b, k) }
    return nil
}

private func firstPair(_ node: Any) -> (String, String)? {
    if let dict = node as? [String: Any] {
        if let pair = pairIn(dict) { return pair }
        for value in dict.values {
            if let hit = firstPair(value) { return hit }
        }
    } else if let arr = node as? [Any] {
        for item in arr {
            if let hit = firstPair(item) { return hit }
        }
    }
    return nil
}

// MARK: - CC Switch

struct CCPair {
    var app: String
    var name: String
    var base: String
    var key: String
    var current: Bool
}

func looksLikeKey(_ text: String) -> Bool {
    guard text.count >= 20, text.count <= 200 else { return false }
    if text.hasPrefix("http") || text.contains(" ") || text.contains("/") { return false }
    let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    return text.unicodeScalars.allSatisfy { allowed.contains($0) }
}

/// 通用兜底：任意层级里找 http 地址和像 Key 的字符串
func scanPair(_ node: Any, base: inout String?, key: inout String?) {
    if let dict = node as? [String: Any] {
        for value in dict.values { scanPair(value, base: &base, key: &key) }
    } else if let arr = node as? [Any] {
        for item in arr { scanPair(item, base: &base, key: &key) }
    } else if let s = node as? String {
        if base == nil && s.hasPrefix("http") {
            base = s.trimmingCharacters(in: .whitespaces)
        } else if key == nil && looksLikeKey(s) {
            key = s
        }
    }
}

func extractPair(_ conf: [String: Any]) -> (String, String)? {
    let env = conf["env"] as? [String: Any] ?? [:]
    let auth = conf["auth"] as? [String: Any] ?? [:]
    let opts = conf["options"] as? [String: Any] ?? [:]

    func envStr(_ keys: [String]) -> String? {
        for k in keys {
            if let v = env[k] as? String, !v.isEmpty { return v }
        }
        return nil
    }

    var base = envStr(["ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "GEMINI_BASE_URL",
                       "GOOGLE_GEMINI_BASE_URL"])
        ?? (opts["baseURL"] as? String)
        ?? (opts["baseUrl"] as? String)
        ?? (auth["base_url"] as? String)

    var key = envStr(["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                      "GEMINI_API_KEY", "GOOGLE_API_KEY"])
        ?? (opts["apiKey"] as? String)
        ?? (auth["OPENAI_API_KEY"] as? String)

    if (base ?? "").isEmpty || (key ?? "").isEmpty,
       let text = conf["config"] as? String,
       let range = text.range(of: #"base_url\s*=\s*["']([^"']+)["']"#,
                              options: .regularExpression) {
        let matched = String(text[range])
        if let q1 = matched.firstIndex(where: { $0 == "\"" || $0 == "'" }),
           let q2 = matched.lastIndex(where: { $0 == "\"" || $0 == "'" }), q1 < q2 {
            let value = String(matched[matched.index(after: q1)..<q2])
            if (base ?? "").isEmpty { base = value }
        }
    }

    if (base ?? "").isEmpty || (key ?? "").isEmpty {
        var b: String?
        var k: String?
        scanPair(conf, base: &b, key: &k)
        if (base ?? "").isEmpty { base = b }
        if (key ?? "").isEmpty { key = k }
    }

    guard let b = base, let k = key, !b.isEmpty, !k.isEmpty else { return nil }
    return (normBase(b), k)
}

/// 读 CC Switch 本地库，列出所有同时带地址和 Key 的供应商。只读，不改库。
func ccProviders() throws -> [CCPair] {
    guard FileManager.default.fileExists(atPath: Paths.ccswitchDB.path) else {
        throw NSError(domain: "relay", code: 4,
                      userInfo: [NSLocalizedDescriptionKey: "没找到 CC Switch 数据库"])
    }
    var db: OpaquePointer?
    let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_URI
    guard sqlite3_open_v2("file:\(Paths.ccswitchDB.path)?mode=ro", &db, flags, nil) == SQLITE_OK else {
        throw NSError(domain: "relay", code: 5,
                      userInfo: [NSLocalizedDescriptionKey: "打不开 CC Switch 数据库"])
    }
    defer { sqlite3_close(db) }

    let sql = """
        select app_type, name, settings_config, is_current from providers
        order by is_current desc, app_type, sort_index
        """
    var stmt: OpaquePointer?
    guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
        throw NSError(domain: "relay", code: 6,
                      userInfo: [NSLocalizedDescriptionKey: "读不了 providers 表"])
    }
    defer { sqlite3_finalize(stmt) }

    func text(_ stmt: OpaquePointer?, _ i: Int32) -> String {
        guard let ptr = sqlite3_column_text(stmt, i) else { return "" }
        return String(cString: ptr)
    }

    var out: [CCPair] = []
    var seen = Set<String>()
    while sqlite3_step(stmt) == SQLITE_ROW {
        let app = text(stmt, 0)
        let name = text(stmt, 1)
        let raw = text(stmt, 2)
        let current = sqlite3_column_int(stmt, 3) != 0

        guard let data = raw.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data),
              let conf = obj as? [String: Any],
              let pair = extractPair(conf) else { continue }

        let key = pair.0 + "\u{1}" + pair.1
        if seen.contains(key) { continue }
        seen.insert(key)
        out.append(CCPair(app: app, name: name, base: pair.0, key: pair.1, current: current))
    }
    return out
}
