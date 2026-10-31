import Foundation

// MARK: - 检查更新

struct UpdateInfo {
    var behind: Int          // 远端领先（本机落后）的提交数
    var ahead: Int           // 本机领先的提交数
    var local: String?
    var remote: String?
    var message: String
    var date: String
    var url: URL
}

enum UpError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let m): return m
        }
    }
}

private final class UpBox<T> {
    var value: T?
}

private func fetchJSON(_ url: URL, timeout: TimeInterval = 15) throws -> Any {
    var req = URLRequest(url: url)
    req.timeoutInterval = timeout
    req.setValue("relay-balance-updater", forHTTPHeaderField: "User-Agent")
    req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")

    let sem = DispatchSemaphore(value: 0)
    let payload = UpBox<Any>()
    let failure = UpBox<Error>()

    URLSession.shared.dataTask(with: req) { data, response, error in
        defer { sem.signal() }
        if let error = error {
            failure.value = UpError.message(error.localizedDescription)
            return
        }
        guard let data = data else {
            failure.value = UpError.message("没有返回内容")
            return
        }
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            let text = http.statusCode == 403 ? "GitHub 限流，稍后再试" : "GitHub 返回 \(http.statusCode)"
            failure.value = UpError.message(text)
            return
        }
        if let obj = try? JSONSerialization.jsonObject(with: data) {
            payload.value = obj
        } else {
            failure.value = UpError.message("返回不是 JSON")
        }
    }.resume()

    _ = sem.wait(timeout: .now() + timeout + 5)
    if let err = failure.value { throw err }
    guard let obj = payload.value else { throw UpError.message("请求超时") }
    return obj
}

enum Updater {
    static let repo = "brookLyn1102/relay-balance"
    static let branch = "main"
    static let version = "1.4.0"

    static var homePage: URL { URL(string: "https://github.com/\(repo)")! }

    /// 构建时由 scripts/build-app.sh 写进 Info.plist；`swift run` 时取不到
    static var localCommit: String? {
        guard let s = Bundle.main.object(forInfoDictionaryKey: "RelayCommit") as? String,
              s.count >= 7, s != "dev" else { return nil }
        return s
    }

    static func check() throws -> UpdateInfo {
        if let local = localCommit {
            let url = URL(string: "https://api.github.com/repos/\(repo)/compare/\(local)...\(branch)")!
            guard let obj = try fetchJSON(url) as? [String: Any] else {
                throw UpError.message("返回格式不对")
            }
            // compare 的 base 是本机、head 是远端：ahead_by 是远端领先几个
            var info = UpdateInfo(
                behind: (obj["ahead_by"] as? NSNumber)?.intValue ?? 0,
                ahead: (obj["behind_by"] as? NSNumber)?.intValue ?? 0,
                local: String(local.prefix(7)),
                remote: String(local.prefix(7)),
                message: "",
                date: "",
                url: homePage)
            if let commits = obj["commits"] as? [[String: Any]], let last = commits.last {
                fill(&info, with: last)
            }
            return info
        }

        // 没有本地提交号（`swift run` 起的开发版）：只能看远端最新
        let url = URL(string: "https://api.github.com/repos/\(repo)/commits/\(branch)")!
        let raw = try fetchJSON(url)
        var obj: [String: Any]
        if let list = raw as? [[String: Any]] {
            obj = list.first ?? [:]
        } else if let dict = raw as? [String: Any] {
            obj = dict
        } else {
            throw UpError.message("返回格式不对")
        }
        var info = UpdateInfo(behind: 0, ahead: 0, local: nil, remote: nil,
                              message: "", date: "", url: homePage)
        fill(&info, with: obj)
        return info
    }

    private static func fill(_ info: inout UpdateInfo, with commit: [String: Any]) {
        if let sha = commit["sha"] as? String {
            info.remote = String(sha.prefix(7))
        }
        if let link = commit["html_url"] as? String, let u = URL(string: link) {
            info.url = u
        }
        if let detail = commit["commit"] as? [String: Any] {
            let msg = (detail["message"] as? String) ?? ""
            info.message = msg.components(separatedBy: "\n").first ?? ""
            if let author = detail["author"] as? [String: Any],
               let date = author["date"] as? String {
                info.date = date
            }
        }
    }
}
