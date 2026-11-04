import AppKit
import SwiftUI

// MARK: - 配色

enum Palette {
    static let bg = Color(hex: "#15171c")
    static let card = Color(hex: "#1b1e25")
    static let line = Color(hex: "#262a33")
    static let text = Color(hex: "#e8eaed")
    static let muted = Color(hex: "#8b93a1")
    static let green = Color(hex: "#34d399")
    static let amber = Color(hex: "#f5b544")
    static let red = Color(hex: "#f87171")
    static let track = Color(hex: "#2a2f38")
    static let up = Color(hex: "#5aa9ff")
    static let ring = Color(hex: "#3a4150")
}

let INTERVALS: [(Int, String)] = [
    (30, "30 秒"), (60, "1 分钟"), (120, "2 分钟"),
    (300, "5 分钟"), (600, "10 分钟"), (1800, "30 分钟"),
]

// MARK: - 刷新间隔下拉

/// 刷新间隔下拉。
///
/// 不用 `.pickerStyle(.menu)`：它背后是 NSPopUpButton，自带一块灰色胶囊底，
/// 压在深色面板上很突兀，而且底色改不掉。这里换成 Menu + borderlessButton：
/// 透明底、白字，右侧只留系统画的小箭头，和面板里其它文字齐平。
/// （`.menuStyle(.button)` 也能做到，但要 macOS 13+，这个包最低支持 12。）
struct IntervalMenu: View {
    var value: Int
    var size: CGFloat = 10
    var onChange: (Int) -> Void

    private var currentLabel: String {
        INTERVALS.first { $0.0 == value }?.1 ?? "\(value) 秒"
    }

    var body: some View {
        Menu {
            ForEach(INTERVALS, id: \.0) { item in
                Button {
                    onChange(item.0)
                } label: {
                    if item.0 == value {
                        Label(item.1, systemImage: "checkmark")
                    } else {
                        Text(item.1)
                    }
                }
            }
        } label: {
            Text(currentLabel)
                .font(.system(size: size))
                .foregroundColor(Palette.text)
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
    }
}

// MARK: - 面板

struct PanelView: View {
    @ObservedObject var model: Model
    var onClose: () -> Void
    var onSettings: () -> Void
    var onUsage: () -> Void

    @State private var hoverRefresh = false
    @State private var hoverGear = false
    @State private var hoverClose = false

    private var pct: Double {
        guard let w = model.week else { return 0 }
        return max(0, min(100, w.pct))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.bottom, 10)

            // 取消勾选「余额」后整块不显示：这时候后台也不再请求中转接口
            if model.cfg.menuBalance {
                HStack(alignment: .firstTextBaseline) {
                    Text(model.balance.map { fmtMoney($0.left) } ?? "—")
                        .font(.system(size: 27, weight: .semibold, design: .rounded))
                        .foregroundColor((model.balance?.left ?? 0) < 0 ? Palette.red : Palette.text)
                    Spacer()
                    if model.week != nil {
                        Text(pct >= 1000 ? "本周 >999%" : String(format: "本周 %.0f%%", pct))
                            .font(.system(size: 11))
                            .foregroundColor(model.pctColor(pct))
                    }
                }

                progress
                    .padding(.top, 8)
            }

            VStack(alignment: .leading, spacing: 4) {
                if model.cfg.menuBalance, let w = model.week {
                    Text(model.weekText(w))
                }
                if model.cfg.menuBalance, let b = model.balance {
                    Text(String(format: "总额度 $%.2f   ·   已用 $%.2f", b.total, b.used))
                }
                if let t = model.todayText {
                    Text(t)
                }
                if model.cfg.menuMem, let m = model.mem {
                    Text(String(format: "内存 %.1fG 可用 · 已用 %.0f%%", m.availableGB, m.usedPct))
                        .foregroundColor(m.usedPct >= 90 ? Palette.red
                                         : (m.usedPct >= 80 ? Palette.amber : Palette.muted))
                }
                if model.cfg.menuTemp, let t = model.cpuTemp {
                    Text(String(format: "CPU %.0f°C · 平均 %d 个核心", t, CPUTemp.shared.coreCount))
                        .foregroundColor(t >= 80 ? Palette.red : (t >= 65 ? Palette.amber : Palette.muted))
                }
            }
            .font(.system(size: 11))
            .foregroundColor(Palette.muted)
            .padding(.top, 10)

            if model.cfg.netPanel {
                HStack {
                    Text("↑ " + fmtSpeed(model.up)).foregroundColor(Palette.up)
                    Spacer()
                    Text("↓ " + fmtSpeed(model.down)).foregroundColor(Palette.green)
                }
                .font(.system(size: 11, design: .monospaced))
                .padding(.top, 8)
            }

            Divider().background(Palette.line).padding(.top, 10)

            HStack {
                Text(model.statusText)
                    .font(.system(size: 10))
                    .foregroundColor(model.statusColor)
                Spacer()
                if let info = model.update, info.behind > 0 {
                    Button {
                        NSWorkspace.shared.open(info.url)
                    } label: {
                        Text("可更新")
                            .font(.system(size: 10))
                            .foregroundColor(Palette.green)
                    }
                    .buttonStyle(.plain)
                    .help(info.message)
                }
                IntervalMenu(value: model.cfg.interval) { model.setInterval($0) }
                    .disabled(model.nightIdle)
            }
            .padding(.top, 6)
        }
        .padding(16)
        .frame(width: 326)
        .background(Palette.bg)
    }

    private var header: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(model.statusColor)
                .frame(width: 7, height: 7)
            Text(APP_NAME)
                .font(.system(size: 12))
                .foregroundColor(Palette.muted)
            Spacer()

            Button(action: { model.refresh() }) {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(model.busy ? Palette.green : (hoverRefresh ? Palette.text : Palette.muted))
                    .frame(width: 17, height: 17)
                    .overlay(
                        Circle()
                            .stroke(model.busy ? Palette.green : (hoverRefresh ? Palette.muted : Palette.ring),
                                    lineWidth: 1)
                    )
            }
            .buttonStyle(.plain)
            .onHover { hoverRefresh = $0 }
            .disabled(!model.cfg.menuBalance)
            .opacity(model.cfg.menuBalance ? 1 : 0.35)
            .help(model.cfg.menuBalance ? "立即刷新" : "余额查询已关闭")

            Button(action: onSettings) {
                Image(systemName: "gearshape")
                    .font(.system(size: 12))
                    .foregroundColor(hoverGear ? Palette.text : Palette.muted)
            }
            .buttonStyle(.plain)
            .onHover { hoverGear = $0 }
            .help("设置 / 换 Key")

            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(hoverClose ? Palette.text : Palette.muted)
            }
            .buttonStyle(.plain)
            .onHover { hoverClose = $0 }
            .help("关闭")
        }
    }

    private var progress: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 3).fill(Palette.track)
                RoundedRectangle(cornerRadius: 3)
                    .fill(model.pctColor(pct))
                    .frame(width: geo.size.width * CGFloat(pct / 100))
            }
        }
        .frame(height: 6)
    }
}
