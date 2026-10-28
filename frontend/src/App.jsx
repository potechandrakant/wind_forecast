import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const API_BASE = import.meta.env.VITE_API_BASE || "";
const defaultDate = "2026-01-02";

async function getJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function formatMw(value) {
  return value == null ? "—" : `${Math.round(value).toLocaleString()} MW`;
}

function formatPercent(value) {
  return value == null ? "—" : `${value.toFixed(1)}%`;
}

function MetricCard({ label, value, help }) {
  return (
    <article className="metric-card">
      <p className="eyebrow">{label}</p>
      <strong>{value}</strong>
      <p className="muted">{help}</p>
    </article>
  );
}

function LoadingState() {
  return <div className="state-card"><span className="spinner" /> Loading the latest snapshot…</div>;
}

function ErrorState({ error, retry }) {
  return (
    <div className="state-card error-state">
      <strong>We could not load this view.</strong>
      <span>{error.message}</span>
      <button className="button secondary" onClick={retry}>Try again</button>
    </div>
  );
}

function EmptyState({ children = "No data is available for this selection." }) {
  return <div className="state-card">{children}</div>;
}

function DecisionView({ dates }) {
  const [deliveryDate, setDeliveryDate] = useState(dates[0] || defaultDate);
  const [cutoff, setCutoff] = useState("09:00");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    setError(null);
    getJson(`/api/decision?delivery_date=${deliveryDate}&cutoff=${cutoff}`)
      .then(setResult)
      .catch(setError);
  };
  useEffect(load, [deliveryDate, cutoff]);

  if (error) return <ErrorState error={error} retry={load} />;
  if (!result) return <LoadingState />;

  const chartData = result.records.map((row) => ({
    time: row.startTime.slice(11, 16),
    forecast: row.forecast,
    actual: row.actual,
    error: row.error,
  }));
  const usable = result.records.filter((row) => row.status === "ok");
  const totalForecast = usable.reduce((sum, row) => sum + row.forecast * 0.5, 0);
  const publishTimes = [...new Set(usable.map((row) => row.publishTime?.slice(11, 16)).filter(Boolean))];

  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="eyebrow">Decision view</p>
          <h2>What did the buyer know at 09:00?</h2>
          <p className="lede">Compare the forecast available on D-1 with what actually arrived in each half-hour.</p>
        </div>
        <div className="controls">
          <label>Delivery date<select value={deliveryDate} onChange={(event) => setDeliveryDate(event.target.value)}>{dates.map((date) => <option key={date}>{date}</option>)}</select></label>
          <label>Cut-off<select value={cutoff} onChange={(event) => setCutoff(event.target.value)}><option>09:00</option><option>08:00</option><option>10:00</option><option>12:00</option></select></label>
        </div>
      </div>
      <div className="metric-grid">
        <MetricCard label="Forecast energy" value={`${Math.round(totalForecast).toLocaleString()} MWh`} help="Sum of the 48 forecast MW values × 0.5 hours." />
        <MetricCard label="Periods covered" value={`${usable.length} / 48`} help="Periods with an eligible pre-cut-off forecast." />
        <MetricCard label="Forecast vintage" value={publishTimes.length ? publishTimes.join(", ") : "—"} help="Publication time(s) used by the selected decision." />
        <MetricCard label="Cut-off in effect" value={cutoff} help={`Europe/London on ${deliveryDate} D-1.`} />
      </div>
      <div className="panel chart-panel">
        <div className="panel-heading"><h3>Forecast versus actual generation</h3><span className="legend-note">MW · half-hourly</span></div>
        <ResponsiveContainer width="100%" height={320}>
          <AreaChart data={chartData}>
            <defs><linearGradient id="actualFill" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#27c29a" stopOpacity={0.28} /><stop offset="95%" stopColor="#27c29a" stopOpacity={0} /></linearGradient></defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#dce8e7" />
            <XAxis dataKey="time" interval={3} />
            <YAxis tickFormatter={(value) => `${Math.round(value / 1000)}k`} />
            <Tooltip formatter={(value) => formatMw(value)} />
            <Legend />
            <Area type="monotone" dataKey="actual" stroke="#159a7b" fill="url(#actualFill)" strokeWidth={2} connectNulls name="Actual" />
            <Line type="monotone" dataKey="forecast" stroke="#ef8354" strokeWidth={2} dot={false} connectNulls name="Forecast used" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="panel">
        <div className="panel-heading"><h3>48 half-hours</h3><button className="button secondary" onClick={() => downloadCsv(result.records, "decision.csv")}>Export CSV</button></div>
        <div className="table-wrap"><table><thead><tr><th>Time</th><th>Forecast</th><th>Published</th><th>Actual</th><th>Error</th><th>Status</th></tr></thead><tbody>{result.records.map((row) => <tr key={row.startTime}><td>{row.startTime.slice(11, 16)}</td><td>{formatMw(row.forecast)}</td><td>{row.publishTime?.slice(11, 16) || "—"}</td><td>{formatMw(row.actual)}</td><td className={row.error > 0 ? "positive" : ""}>{formatMw(row.error)}</td><td><span className={`status ${row.status}`}>{row.status === "ok" ? "Ready" : "Missing"}</span></td></tr>)}</tbody></table></div>
      </div>
    </section>
  );
}

function AccuracyView({ range }) {
  const [summary, setSummary] = useState(null);
  const [byHour, setByHour] = useState(null);
  const [byHorizon, setByHorizon] = useState(null);
  const [distribution, setDistribution] = useState(null);
  const [error, setError] = useState(null);
  const query = `start=${range.start}&end=${range.end}`;
  const load = () => {
    setError(null);
    Promise.all([
      getJson(`/api/accuracy/summary?${query}`),
      getJson(`/api/accuracy/by-hour?${query}`),
      getJson(`/api/accuracy/by-horizon?${query}`),
      getJson(`/api/accuracy/error-distribution?${query}`),
    ]).then(([nextSummary, nextHour, nextHorizon, nextDistribution]) => {
      setSummary(nextSummary); setByHour(nextHour); setByHorizon(nextHorizon); setDistribution(nextDistribution);
    }).catch(setError);
  };
  useEffect(load, [range.start, range.end]);
  if (error) return <ErrorState error={error} retry={load} />;
  if (!summary) return <LoadingState />;

  const hours = Object.entries(byHour.groups).map(([hour, metrics]) => ({ hour, mae: metrics.mae_mw, bias: metrics.bias_mw }));
  const horizons = Object.entries(byHorizon.groups).map(([horizon, metrics]) => ({ horizon, mae: metrics.mae_mw, bias: metrics.bias_mw }));
  return (
    <section>
      <div className="section-heading"><div><p className="eyebrow">Accuracy dashboard</p><h2>Turn forecast error into evidence</h2><p className="lede">Use the observed error profile to understand where a day-ahead forecast is reliable—and where it needs cover.</p></div></div>
      <div className="metric-grid">
        <MetricCard label="MAE" value={formatMw(summary.metrics.mae_mw)} help="Typical absolute error, regardless of direction." />
        <MetricCard label="RMSE" value={formatMw(summary.metrics.rmse_mw)} help="Penalises large misses more heavily than MAE." />
        <MetricCard label="Bias" value={formatMw(summary.metrics.bias_mw)} help="Positive means the forecast over-predicted wind." />
        <MetricCard label="P95 absolute error" value={formatMw(summary.metrics.absolute_error_p95_mw)} help="Only 5% of observed absolute errors were larger." />
        <MetricCard label="MAPE" value={formatPercent(summary.metrics.mape_percent)} help="Average percentage error, excluding near-zero actuals." />
        <MetricCard label="Normalised MAE" value={formatPercent((summary.metrics.normalised_mae || 0) * 100)} help="MAE relative to average actual generation." />
      </div>
      <div className="chart-grid">
        <div className="panel"><div className="panel-heading"><h3>Error by hour</h3><span className="legend-note">MAE and bias</span></div><ResponsiveContainer width="100%" height={280}><BarChart data={hours}><CartesianGrid strokeDasharray="3 3" stroke="#dce8e7" /><XAxis dataKey="hour" interval={2} /><YAxis tickFormatter={(value) => `${Math.round(value / 1000)}k`} /><Tooltip formatter={(value) => formatMw(value)} /><Legend /><Bar dataKey="mae" fill="#ef8354" name="MAE" radius={[4, 4, 0, 0]} /><Bar dataKey="bias" fill="#4a77b8" name="Bias" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div>
        <div className="panel"><div className="panel-heading"><h3>Error by horizon</h3><span className="legend-note">Longer horizon usually means more uncertainty</span></div><ResponsiveContainer width="100%" height={280}><BarChart data={horizons}><CartesianGrid strokeDasharray="3 3" stroke="#dce8e7" /><XAxis dataKey="horizon" /><YAxis tickFormatter={(value) => `${Math.round(value / 1000)}k`} /><Tooltip formatter={(value) => formatMw(value)} /><Legend /><Bar dataKey="mae" fill="#27c29a" name="MAE" radius={[4, 4, 0, 0]} /><Bar dataKey="bias" fill="#4a77b8" name="Bias" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div>
      </div>
      <div className="panel"><div className="panel-heading"><h3>Error distribution</h3><span className="legend-note">Forecast − actual · MW</span></div><ResponsiveContainer width="100%" height={280}><BarChart data={distribution.bins}><CartesianGrid strokeDasharray="3 3" stroke="#dce8e7" /><XAxis dataKey="lower_mw" tickFormatter={(value) => `${Math.round(value / 1000)}k`} /><YAxis /><Tooltip formatter={(value) => `${value} periods`} /><Bar dataKey="count" fill="#ef8354" name="Periods" /></BarChart></ResponsiveContainer></div>
    </section>
  );
}

function RiskView({ range }) {
  const [confidence, setConfidence] = useState(0.95);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const load = () => {
    setError(null);
    getJson(`/api/risk-buffer?start=${range.start}&end=${range.end}&confidence=${confidence}`).then(setResult).catch(setError);
  };
  useEffect(load, [range.start, range.end, confidence]);
  if (error) return <ErrorState error={error} retry={load} />;
  if (!result) return <LoadingState />;
  const hours = Object.entries(result.by_hour).map(([hour, buffer]) => ({ hour, buffer }));
  return (
    <section>
      <div className="section-heading"><div><p className="eyebrow">Risk planning</p><h2>How much cover would have been enough?</h2><p className="lede">A positive error means wind was over-forecast. Use this buffer as a transparent planning input—not a guarantee.</p></div><label className="confidence-control">Confidence <strong>{Math.round(confidence * 100)}%</strong><input type="range" min="0.5" max="0.99" step="0.01" value={confidence} onChange={(event) => setConfidence(Number(event.target.value))} /></label></div>
      <div className="risk-hero"><div><p className="eyebrow">Observed over-forecast buffer</p><strong>{formatMw(result.buffer_mw)}</strong><p>At {Math.round(confidence * 100)}% confidence, this extra cover would have absorbed the positive forecast errors in the selected window.</p></div><div className="risk-ring" style={{ "--progress": `${confidence * 100}%` }}><span>{Math.round(confidence * 100)}%</span></div></div>
      <div className="panel"><div className="panel-heading"><h3>Buffer by hour of day</h3><span className="legend-note">Positive error percentile · MW</span></div><ResponsiveContainer width="100%" height={330}><BarChart data={hours}><CartesianGrid strokeDasharray="3 3" stroke="#dce8e7" /><XAxis dataKey="hour" interval={2} /><YAxis tickFormatter={(value) => `${Math.round(value / 1000)}k`} /><Tooltip formatter={(value) => formatMw(value)} /><ReferenceLine y={result.buffer_mw} stroke="#ef8354" strokeDasharray="6 4" /><Bar dataKey="buffer" name="Buffer" radius={[5, 5, 0, 0]}>{hours.map((entry) => <Cell key={entry.hour} fill={entry.buffer >= result.buffer_mw ? "#ef8354" : "#27c29a"} />)}</Bar></BarChart></ResponsiveContainer></div>
      <div className="assumption-panel"><strong>How to read this</strong><p>The buffer is calculated from historical positive errors only. It does not account for market price, correlation between half-hours, or operational constraints. Pair it with the accuracy dashboard and the buyer’s commercial view.</p></div>
    </section>
  );
}

function downloadCsv(rows, filename) {
  const headers = Object.keys(rows[0] || {});
  const csv = [headers.join(","), ...rows.map((row) => headers.map((header) => JSON.stringify(row[header] ?? "")).join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const link = document.createElement("a");
  link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
}

export default function App() {
  const [meta, setMeta] = useState(null);
  const [metaError, setMetaError] = useState(null);
  const [view, setView] = useState("decision");
  const [range, setRange] = useState({ start: defaultDate, end: "2026-02-01" });
  useEffect(() => { getJson("/api/meta").then((data) => { setMeta(data); setRange({ start: data.available_dates[0], end: data.available_dates.at(-1) }); }).catch(setMetaError); }, []);
  const dates = meta?.available_dates || [];
  const rangeControls = useMemo(() => <div className="range-controls"><label>From<select value={range.start} onChange={(event) => setRange({ ...range, start: event.target.value })}>{dates.map((date) => <option key={date}>{date}</option>)}</select></label><label>To<select value={range.end} onChange={(event) => setRange({ ...range, end: event.target.value })}>{dates.map((date) => <option key={date}>{date}</option>)}</select></label></div>, [dates, range]);
  if (metaError) return <main className="app-shell"><ErrorState error={metaError} retry={() => window.location.reload()} /></main>;
  if (!meta) return <main className="app-shell"><LoadingState /></main>;
  return (
    <main className="app-shell">
      <header className="topbar"><div className="brand"><div className="brand-mark">W</div><div><strong>Windwise</strong><span>UK wind forecast portal</span></div></div><div className="topbar-status"><span className="live-dot" /> Snapshot loaded · {meta.actual_rows.toLocaleString()} actuals</div></header>
      <section className="hero"><div><p className="eyebrow">Day-ahead decision support · January 2026</p><h1>Buy with a clearer view of the wind.</h1><p className="hero-copy">See the forecast your team could actually use, measure how it performed, and put a transparent MW buffer behind the uncertainty.</p></div><div className="hero-stat"><span>Default decision cut-off</span><strong>{meta.default_cutoff}</strong><small>{meta.timezone} · D-1</small></div></section>
      <nav className="tabs">{[["decision", "Decision view"], ["accuracy", "Accuracy dashboard"], ["risk", "Risk planning"]].map(([key, label]) => <button key={key} className={view === key ? "active" : ""} onClick={() => setView(key)}>{label}</button>)}</nav>
      {view !== "decision" && <div className="range-bar">{rangeControls}<span className="range-note">Showing {range.start} to {range.end}</span></div>}
      {view === "decision" && <DecisionView dates={dates} />}
      {view === "accuracy" && <AccuracyView range={range} />}
      {view === "risk" && <RiskView range={range} />}
      <footer><span>Windwise · Analysis based on Elexon BMRS FUELHH and WINDFOR snapshots.</span><button onClick={() => document.getElementById("assumptions").showModal()}>About & assumptions</button></footer>
      <dialog id="assumptions"><button className="dialog-close" onClick={() => document.getElementById("assumptions").close()}>×</button><p className="eyebrow">About this portal</p><h2>Transparent by design.</h2><p>January decisions mean D-1 decisions for delivery dates 2 January–1 February 2026. Forecasts are selected at the 09:00 Europe/London cut-off. WINDFOR is hourly in this snapshot, so the hourly forecast is repeated for the next half-hour without interpolation.</p><p>Positive error means over-forecast wind. Missing forecasts are flagged, never silently filled. This prototype uses a committed data snapshot to keep deployment reliable.</p></dialog>
    </main>
  );
}
