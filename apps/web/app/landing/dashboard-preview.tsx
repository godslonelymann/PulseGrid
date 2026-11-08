"use client";

import { Activity, Globe, Layers, Pause, Play, RotateCcw } from "lucide-react";
import {
  animate,
  useMotionValue,
  useMotionValueEvent,
  useReducedMotion,
  type AnimationPlaybackControls,
} from "motion/react";
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import styles from "./landing.module.css";

const firstFailure = 18;
const incidentOpened = firstFailure + 1;
const incidentRecovered = incidentOpened + 2;
const checks = Array.from({ length: 24 }, (_, index) => {
  const failed = index >= firstFailure && index <= incidentOpened;
  return {
    time: `14:${String(Math.floor(index / 2)).padStart(2, "0")}:${index % 2 === 0 ? "00" : "30"}`,
    result: failed ? "HTTP 503" : "HTTP 200",
    failed,
    status:
      index >= incidentRecovered
        ? "Recovered"
        : index >= incidentOpened
          ? "Down"
          : "Up",
    latency: failed
      ? null
      : [
          142, 158, 149, 172, 160, 153, 166, 147, 158, 182, 163, 155, 170, 161,
          153, 184, 173, 198, 0, 0, 188, 165, 154, 148,
        ][index],
  };
});
const steps = [
  { label: "Healthy", time: 0 },
  { label: "Outage", time: 1100 },
  { label: "Recovered", time: 3400 },
];
// Fixed directional connections adapted from EasyUI Dependency Trace; the moving
// highlight follows Aceternity's SVG beam pattern, driven by replay time, not scroll.
const paths = [
  "M 292 102 C 252 72 199 72 156 102",
  "M 156 126 C 205 158 248 158 292 126",
  "M 396 114 L 532 114",
];
const signals = [
  { start: 0, path: 0 },
  { start: 200, path: 1 },
  { start: 500, path: 0 },
  { start: 800, path: 1 },
  { start: 1100, path: 2 },
  { start: 1400, path: 0 },
  { start: 1700, path: 1 },
  { start: 2100, path: 0 },
  { start: 2400, path: 1 },
  { start: 2700, path: 2 },
];

export function IncidentHero() {
  const clock = useMotionValue(3400);
  const [time, setTime] = useState(3400);
  const [playing, setPlaying] = useState(false);
  const playback = useRef<AnimationPlaybackControls | null>(null);
  const panel = useRef<HTMLElement>(null);
  const reduced = useReducedMotion();
  const arrow = useId().replaceAll(":", "");
  useMotionValueEvent(clock, "change", setTime);
  useEffect(() => () => playback.current?.stop(), []);
  useEffect(() => {
    if (reduced) {
      playback.current?.stop();
    }
  }, [reduced]);
  const running = playing && !reduced;
  function seek(value: number) {
    playback.current?.stop();
    setPlaying(false);
    clock.set(value);
  }
  function play(restart = false) {
    playback.current?.stop();
    if (reduced) {
      seek(3400);
      return;
    }
    const start = restart || clock.get() >= 3400 ? 0 : clock.get();
    clock.set(start);
    setPlaying(true);
    playback.current = animate(clock, 3400, {
      duration: (3400 - start) / 1000,
      ease: "linear",
      onComplete: () => setPlaying(false),
    });
  }
  const down = time >= 1100 && time < 2700;
  const failed = time >= 500 && time < 2000;
  const active = time >= 2700 ? 2 : time >= 1100 ? 1 : 0;
  const event =
    time >= 2700
      ? "Recovery alert queued"
      : time >= 2000
        ? "First success · incident still open"
        : down
          ? "Incident opened · outage alert queued"
          : failed
            ? "First failure"
            : "Endpoint responding";
  const controlLabel = running
    ? "Pause incident"
    : time >= 3400
      ? "Replay incident"
      : time === 0
        ? "Play incident"
        : "Resume incident";
  const Control = running ? Pause : time >= 3400 ? RotateCcw : Play;
  return (
    <section
      className={`${styles.container} ${styles.hero}`}
      aria-labelledby="landing-title"
    >
      <div className={styles.heroCopy}>
        <div className={styles.eyebrow}>Self-hosted uptime monitoring</div>
        <h1 id="landing-title">
          Know when it
          <br />
          goes down.
        </h1>
        <p>
          Check your websites and APIs from your own server. See when an outage
          starts, how long it lasts, and when the service recovers.
        </p>
        <div className={styles.actions}>
          <Link
            className={styles.primary}
            href="/?auth=signup"
            prefetch={false}
          >
            Start monitoring
          </Link>
          <button
            className={styles.secondary}
            onClick={() => {
              play(true);
              panel.current?.scrollIntoView({
                behavior: reduced ? "instant" : "smooth",
                block: "nearest",
              });
            }}
          >
            Watch an outage <Play size={14} aria-hidden="true" />
          </button>
        </div>
      </div>
      <section
        ref={panel}
        className={styles.diagram}
        aria-label="Sample incident"
      >
        <div className={styles.diagramTitle}>
          <h2>Sample incident</h2>
          <span className={styles.status} data-down={down} role="status">
            {steps[active].label}
          </span>
        </div>
        <div className={styles.diagramArea}>
          <svg
            className={styles.connections}
            viewBox="0 0 688 244"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <defs>
              <marker
                id={arrow}
                viewBox="0 0 8 8"
                refX="7"
                refY="4"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 8 4 L 0 8" fill="none" stroke="#8b9f8f" />
              </marker>
            </defs>
            {[72, 96, 120, 144].map((r) => (
              <circle key={r} cx="344" cy="114" r={r} className={styles.ring} />
            ))}
            {paths.map((d) => (
              <path
                key={d}
                d={d}
                fill="none"
                stroke="#718675"
                strokeWidth="1"
                markerEnd={`url(#${arrow})`}
              />
            ))}
            {!reduced &&
              signals.map(
                ({ start, path }, i) =>
                  time >= start &&
                  time < start + 300 && (
                    <path
                      key={i}
                      d={paths[path]}
                      fill="none"
                      stroke="#C7F477"
                      strokeWidth="6"
                      vectorEffect="non-scaling-stroke"
                      strokeLinecap="round"
                      pathLength="1"
                      strokeDasharray="0.12 1"
                      strokeDashoffset={-((time - start) / 300) * 0.88}
                    />
                  ),
              )}
          </svg>
          <span className={`${styles.connectorLabel} ${styles.requestLabel}`}>
            HTTP check
          </span>
          <span className={`${styles.connectorLabel} ${styles.resultLabel}`}>
            Result
          </span>
          <span className={`${styles.connectorLabel} ${styles.queueLabel}`}>
            State change
          </span>
          <div className={`${styles.node} ${styles.apiNode}`}>
            <div className={styles.nodeBox}>
              <Globe aria-hidden="true" />
            </div>
            <span className={styles.desktopLabel}>Checkout API</span>
            <span className={styles.mobileLabel}>API</span>
          </div>
          <div
            className={`${styles.node} ${styles.workerNode}`}
            data-down={down}
          >
            <div className={styles.nodeBox}>
              <Activity aria-hidden="true" />
              <i />
            </div>
            <span className={styles.desktopLabel}>PulseGrid worker</span>
            <span className={styles.mobileLabel}>Worker</span>
          </div>
          <div className={`${styles.node} ${styles.queueNode}`}>
            <div className={styles.nodeBox}>
              <Layers aria-hidden="true" />
            </div>
            <span>Alert queue</span>
          </div>
        </div>
        <div
          className={styles.incidentReadout}
          aria-live="polite"
          aria-atomic="true"
        >
          <span data-failed={failed}>{failed ? "HTTP 503" : "HTTP 200"}</span>
          <span>{event}</span>
        </div>
        <div className={styles.player}>
          <div className={styles.steps} aria-label="Incident steps">
            {steps.map((step, index) => (
              <button
                key={step.label}
                aria-pressed={index === active}
                onClick={() => seek(step.time)}
              >
                {step.label}
              </button>
            ))}
          </div>
          <button
            className={styles.play}
            onClick={() => (running ? seek(clock.get()) : play())}
            aria-label={controlLabel}
            title={controlLabel}
          >
            <Control size={16} aria-hidden="true" />
            <span>{controlLabel}</span>
          </button>
        </div>
      </section>
    </section>
  );
}

export function CheckHistory() {
  const [selected, setSelected] = useState(23);
  const check = checks[selected];
  return (
    <section className={styles.history} aria-label="Sample service history">
      <div className={styles.historyHeader}>
        <div>
          <strong>Checkout API</strong>
          <span>checkout.example.com/health</span>
        </div>
        <span className={styles.sampleLabel}>Sample data</span>
      </div>
      <div
        className={styles.chart}
        role="img"
        aria-label="Sample response times with a gap for two failed checks and one shaded outage interval"
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={checks}
            margin={{ top: 20, right: 16, bottom: 0, left: 0 }}
          >
            <CartesianGrid
              vertical={false}
              stroke="#dce0d4"
              strokeDasharray="3 4"
            />
            <XAxis
              dataKey="time"
              ticks={[
                checks[0].time,
                checks[8].time,
                checks[16].time,
                checks[23].time,
              ]}
              tickFormatter={(v) => v.slice(0, 5)}
              tick={{ fontSize: 10, fill: "#66725e" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[0, 300]}
              ticks={[0, 150, 300]}
              tickFormatter={(v) => `${v}ms`}
              width={48}
              tick={{ fontSize: 10, fill: "#66725e" }}
              axisLine={false}
              tickLine={false}
            />
            <ReferenceArea
              x1={checks[incidentOpened].time}
              x2={checks[incidentRecovered].time}
              fill="#b65c43"
              fillOpacity={0.13}
            />
            <Tooltip formatter={(value) => [`${value} ms`, "Response time"]} />
            <Line
              type="linear"
              dataKey="latency"
              stroke="#526c3c"
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className={styles.checkStrip}>
        <input
          type="range"
          min="0"
          max="23"
          value={selected}
          aria-label="Select a check"
          aria-valuetext={`${check.time}, ${check.result}, ${check.status}`}
          onChange={(e) => setSelected(e.currentTarget.valueAsNumber)}
        />
        <div className={styles.checks} aria-hidden="true">
          {checks.map((c, i) => (
            <span
              key={c.time}
              data-failed={c.failed}
              data-selected={i === selected}
            />
          ))}
        </div>
      </div>
      <div className={styles.historyReadout}>
        <span>
          {check.time} · {check.result}
        </span>
        <span>{check.status}</span>
      </div>
    </section>
  );
}
