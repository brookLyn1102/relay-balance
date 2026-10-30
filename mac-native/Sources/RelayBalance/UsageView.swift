import SwiftUI

struct UsageView: View {
    var onClose: () -> Void

    @State private var days: [String: DayAgg] = [:]
    @State private var loading = true
    @State private var hoverRefresh = false

    private var today: DayAgg { UsageReader.shared.totals(days, dates: [Day.today()]) }
    private var week: DayAgg { UsageReader.shared.totals(days, dates: Day.weekDates()) }
    private var month: DayAgg { UsageReader.shared.totals(days, dates: Day.monthDates()) }
    private var recent: [(String, DayAgg?)] {
        Day.recentDates(7).map { ($0, days[$0]) }
    }
    private var months: [(String, DayAgg)] {
        Array(UsageReader.shared.byMonth(days).prefix(6))
    }
    private var topModels: [(String, ModelAgg)] {
        month.models.sorted { $0.value.total > $1.value.total }.prefix(5).map { ($0.key, $0.value) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text("用量统计").font(.system(size: 15, weight: .semibold))
                Spacer()
                Button(action: reload) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(hoverRefresh ? Palette.text : Palette.muted)
                        .frame(width: 18, height: 18)
                        .overlay(Circle().stroke(hoverRefresh ? Palette.muted : Palette.ring, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .onHover { hoverRefresh = $0 }
                Button("关闭", action: onClose)
            }

            Text("喜鹊路由日志 + CC Switch 本地库，不需要 token")
                .font(.system(size: 10))
                .foregroundColor(Palette.muted)
                .padding(.top, 3)

            if loading {
                Spacer()
                HStack { Spacer(); ProgressView(); Spacer() }
                Spacer()
            } else {
                cards.padding(.top, 12)
                section("近 7 天")
                recentRows
                section("按月")
                monthRows
                section("本月按模型")
                modelRows
                Spacer(minLength: 6)
            }

            HStack {
                Text(totalLine)
                    .font(.system(size: 10))
                    .foregroundColor(Palette.muted)
                Spacer()
            }
        }
        .padding(18)
        .frame(width: 520, height: 640)
        .background(Palette.bg)
        .foregroundColor(Palette.text)
        .onAppear(perform: reload)
    }

    // MARK: 组件

    private var cards: some View {
        HStack(spacing: 8) {
            card("今日", today)
            card("本周", week)
            card("本月", month)
        }
    }

    private func card(_ title: String, _ agg: DayAgg) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.system(size: 10)).foregroundColor(Palette.muted)
            Text(fmtTokens(agg.total))
                .font(.system(size: 17, weight: .semibold, design: .rounded))
            Text("\(agg.req) 次")
                .font(.system(size: 10))
                .foregroundColor(Palette.muted)
        }
        .padding(9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.card)
        .cornerRadius(6)
    }

    private func section(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 11))
            .foregroundColor(Palette.muted)
            .padding(.top, 14)
            .padding(.bottom, 4)
    }

    private var maxRecent: Int {
        max(1, recent.compactMap { $0.1?.total }.max() ?? 1)
    }

    private var recentRows: some View {
        VStack(spacing: 3) {
            ForEach(recent, id: \.0) { item in
                HStack(spacing: 8) {
                    Text(String(item.0.suffix(5)))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(Palette.muted)
                        .frame(width: 42, alignment: .leading)
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 2).fill(Palette.track)
                            RoundedRectangle(cornerRadius: 2)
                                .fill(Palette.green)
                                .frame(width: geo.size.width * CGFloat(item.1?.total ?? 0)
                                       / CGFloat(maxRecent))
                        }
                    }
                    .frame(height: 5)
                    Text(item.1.map { fmtTokens($0.total) } ?? "—")
                        .font(.system(size: 10, design: .monospaced))
                        .frame(width: 52, alignment: .trailing)
                    Text(item.1.map { "\($0.req) 次" } ?? "")
                        .font(.system(size: 10))
                        .foregroundColor(Palette.muted)
                        .frame(width: 46, alignment: .trailing)
                }
            }
        }
    }

    private var monthRows: some View {
        VStack(spacing: 3) {
            ForEach(months, id: \.0) { item in
                HStack {
                    Text(item.0).font(.system(size: 11, design: .monospaced))
                    Spacer()
                    Text(fmtTokens(item.1.total))
                        .font(.system(size: 11, design: .monospaced))
                    Text("\(item.1.req) 次")
                        .font(.system(size: 10))
                        .foregroundColor(Palette.muted)
                        .frame(width: 60, alignment: .trailing)
                }
            }
            if months.isEmpty {
                Text("还没有按月数据").font(.system(size: 10)).foregroundColor(Palette.muted)
            }
        }
    }

    private var modelRows: some View {
        VStack(spacing: 3) {
            ForEach(topModels, id: \.0) { item in
                HStack {
                    Text(item.0)
                        .font(.system(size: 11))
                        .lineLimit(1)
                    Spacer()
                    Text(fmtTokens(item.1.total))
                        .font(.system(size: 11, design: .monospaced))
                    Text("\(item.1.req) 次")
                        .font(.system(size: 10))
                        .foregroundColor(Palette.muted)
                        .frame(width: 60, alignment: .trailing)
                }
            }
            if topModels.isEmpty {
                Text("本月还没有记录").font(.system(size: 10)).foregroundColor(Palette.muted)
            }
        }
    }

    private var totalLine: String {
        "本月 入 \(fmtTokens(month.tin)) · 出 \(fmtTokens(month.tout)) · 缓存 \(fmtTokens(month.cache))"
    }

    // MARK: 数据

    private func reload() {
        loading = true
        DispatchQueue.global(qos: .userInitiated).async {
            let data = UsageReader.shared.collect(120)
            DispatchQueue.main.async {
                days = data
                loading = false
            }
        }
    }
}
