import { useEffect, useRef, useState } from "react";
import {
  Bot,
  CircleStop,
  History,
  KeyRound,
  Monitor,
  Pause,
  Play,
  RefreshCw,
  Settings,
  ShieldAlert,
} from "lucide-react";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const WS = API.replace(/^http/, "ws");

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!res.ok) {
    throw new Error((await res.text()) || `HTTP ${res.status}`);
  }
  return res.json();
}

function StatusBadge({ status }) {
  return (
    <span className={`badge ${status || "idle"}`}>
      {status || "idle"}
    </span>
  );
}

function TaskPanel({ onRun, busy }) {
  const [task, setTask] = useState("");
  const [provider, setProvider] = useState("auto");
  const [headless, setHeadless] = useState(true);
  const [maxSteps, setMaxSteps] = useState(20);

  const submit = (event) => {
    event.preventDefault();
    if (!task.trim() || busy) return;

    onRun({
      task: task.trim(),
      provider,
      headless,
      max_steps: Number(maxSteps),
    });
  };

  return (
    <form className="card task-card" onSubmit={submit}>
      <div className="card-title">
        <Bot size={18} />
        Task
      </div>

      <textarea
        value={task}
        onChange={(event) => setTask(event.target.value)}
        placeholder="e.g. Open Flipkart, search for wireless keyboards, and summarize the first five results."
        rows={5}
      />

      <div className="form-grid">
        <label>
          Provider
          <select
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
          >
            <option value="auto">Auto fallback</option>
            <option value="groq">Groq</option>
            <option value="gemini">Gemini</option>
            <option value="ollama">Local Ollama</option>
          </select>
        </label>

        <label>
          Max steps
          <input
            type="number"
            min="1"
            max="100"
            value={maxSteps}
            onChange={(event) => setMaxSteps(event.target.value)}
          />
        </label>
      </div>

      <label className="toggle">
        <input
          type="checkbox"
          checked={headless}
          onChange={(event) => setHeadless(event.target.checked)}
        />
        Run browser headless
      </label>

      <button className="primary" disabled={busy || !task.trim()}>
        <Play size={17} />
        Run
      </button>
    </form>
  );
}

function Controls({ runId, status, takeover, userActionReason, onChange }) {
  const action = async (name) => {
    if (!runId) return;
    await api(`/api/runs/${runId}/${name}`, {
      method: "POST",
    });
    onChange?.(name);
  };

  return (
    <div className="controls">
      <button
        onClick={() => action("pause")}
        disabled={!runId || status !== "running"}
      >
        <Pause size={16} />
        Pause
      </button>

      <button
        onClick={() => action("takeover")}
        disabled={
          !runId ||
          takeover ||
          ["completed", "failed", "stopped", "stopping"].includes(status)
        }
      >
        <Monitor size={16} />
        Take Over
      </button>

      <button
        onClick={() => action("resume")}
        disabled={
          !runId ||
          (status !== "paused" && !takeover)
        }
      >
        <Play size={16} />
        {userActionReason === "payment_confirmation"
          ? "Confirm & Resume"
          : "Resume"}
      </button>

      <button
        className="danger"
        onClick={() => action("stop")}
        disabled={
          !runId ||
          ["completed", "failed", "stopped"].includes(status)
        }
      >
        <CircleStop size={16} />
        Stop
      </button>
    </div>
  );
}

function BrowserView({
  runId,
  screenshot,
  takeover,
  viewport,
}) {
  const imageRef = useRef(null);
  const [manualText, setManualText] = useState("");

  const click = async (event) => {
    if (!takeover || !runId || !imageRef.current) return;

    const rect = imageRef.current.getBoundingClientRect();
    const x =
      ((event.clientX - rect.left) / rect.width) *
      viewport.width;
    const y =
      ((event.clientY - rect.top) / rect.height) *
      viewport.height;

    await api(
      `/api/runs/${runId}/manual/click`,
      {
        method: "POST",
        body: JSON.stringify({ x, y }),
      },
    );
  };

  const type = async () => {
    if (!manualText) return;

    await api(
      `/api/runs/${runId}/manual/type`,
      {
        method: "POST",
        body: JSON.stringify({
          text: manualText,
        }),
      },
    );

    setManualText("");
  };

  const key = (keyName) =>
    api(
      `/api/runs/${runId}/manual/key`,
      {
        method: "POST",
        body: JSON.stringify({
          key: keyName,
        }),
      },
    );

  return (
    <div className="card browser-card">
      <div className="card-title">
        <Monitor size={18} />
        Live Browser
        {takeover && (
          <span className="takeover-dot">
            Manual control
          </span>
        )}
      </div>

      <div
        className={`browser-stage ${
          takeover ? "interactive" : ""
        }`}
      >
        {screenshot ? (
          <img
            ref={imageRef}
            onClick={click}
            src={screenshot}
            alt="Live browser"
          />
        ) : (
          <div className="empty-browser">
            Browser frames will appear here after a run starts.
          </div>
        )}
      </div>

      {takeover && (
        <div className="manual-bar">
          <input
            value={manualText}
            onChange={(event) =>
              setManualText(event.target.value)
            }
            onKeyDown={(event) =>
              event.key === "Enter" && type()
            }
            placeholder="Type into focused browser element"
          />
          <button onClick={type}>Type</button>
          <button onClick={() => key("Enter")}>Enter</button>
          <button onClick={() => key("Tab")}>Tab</button>
          <button onClick={() => key("Escape")}>Esc</button>
        </div>
      )}
    </div>
  );
}

function Logs({ steps, status, message }) {
  return (
    <div className="card logs-card">
      <div className="card-title">
        <RefreshCw size={18} />
        Live Status
        <StatusBadge status={status} />
      </div>

      {message && (
        <div className="status-message">
          {message}
        </div>
      )}

      <div className="log-list">
        {!steps.length && (
          <div className="muted">
            No steps yet.
          </div>
        )}

        {steps.map((step, index) => (
          <div
            className={`log-row ${
              step.success === false ? "bad" : "good"
            }`}
            key={`${step.step}-${index}`}
          >
            <div className="step-num">
              {step.step}
            </div>

            <div>
              <div className="log-action">
                {step.action}
                {step.target ? (
                  <>
                    {" "}
                    <code>{step.target}</code>
                  </>
                ) : null}
              </div>

              <div className="reason">
                {step.reasoning}
              </div>

              <div className="meta">
                {step.provider || "—"} ·{" "}
                {step.success === false
                  ? "failed"
                  : "success"}
                {step.result
                  ? ` · ${String(step.result).slice(0, 180)}`
                  : ""}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function HistoryPanel({ onOpen, refreshToken }) {
  const [items, setItems] = useState([]);

  useEffect(() => {
    api("/api/history")
      .then(setItems)
      .catch(() => setItems([]));
  }, [refreshToken]);

  return (
    <div className="card">
      <div className="card-title">
        <History size={18} />
        Task History
      </div>

      <div className="history-list">
        {items.length === 0 && (
          <div className="muted">
            No completed runs yet.
          </div>
        )}

        {items.map((item) => (
          <button
            className="history-row"
            key={item.id}
            onClick={() => onOpen(item.id)}
          >
            <div>
              <strong>{item.task}</strong>
              <small>
                {item.created_at
                  ? new Date(
                      item.created_at,
                    ).toLocaleString()
                  : ""}
              </small>
            </div>

            <div className="history-side">
              <StatusBadge status={item.status} />
              <small>
                {item.step_count} steps ·{" "}
                {item.duration || 0}s
                {item.providers_used?.length
                  ? ` · ${item.providers_used.join(" → ")}`
                  : ""}
              </small>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function SettingsPanel() {
  const [data, setData] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [testing, setTesting] = useState("");
  const [notice, setNotice] = useState("");

  const load = () =>
    api("/api/settings")
      .then(setData)
      .catch((error) => setNotice(error.message));

  useEffect(load, []);

  const saveKey = async (name) => {
    await api("/api/settings/key", {
      method: "POST",
      body: JSON.stringify({
        name,
        value: drafts[name] || "",
      }),
    });

    setDrafts((old) => ({
      ...old,
      [name]: "",
    }));
    setNotice(`${name} saved`);
    load();
  };

  const removeKey = async (name) => {
    await api(`/api/settings/key/${name}`, {
      method: "DELETE",
    });
    setNotice(`${name} removed`);
    load();
  };

  const testKey = async (name) => {
    setTesting(name);
    const result = await api(
      `/api/settings/test-key/${name}`,
      { method: "POST" },
    );
    setNotice(
      result.ok
        ? `${name} is working`
        : `${name}: ${result.error}`,
    );
    setTesting("");
  };

  const test = async (provider) => {
    setTesting(provider);
    const result = await api(
      `/api/settings/test/${provider}`,
      { method: "POST" },
    );

    setNotice(
      result.ok
        ? `${provider} is working`
        : `${provider}: ${result.error}`,
    );
    setTesting("");
  };

  const savePriority = async (providers) => {
    await api("/api/settings/priority", {
      method: "POST",
      body: JSON.stringify({ providers }),
    });
    setNotice("Priority saved");
    load();
  };

  if (!data) {
    return <div className="card">Loading settings…</div>;
  }

  const keyNames = Object.keys(data.keys);

  return (
    <div className="settings-stack">
      <div className="card">
        <div className="card-title">
          <KeyRound size={18} />
          API Keys
        </div>

        {keyNames.map((name) => (
          <div className="key-row" key={name}>
            <div>
              <strong>{name}</strong>
              <small>
                {data.keys[name] ||
                  "Not configured"}
              </small>
            </div>

            <input
              type="password"
              value={drafts[name] || ""}
              onChange={(event) =>
                setDrafts((old) => ({
                  ...old,
                  [name]: event.target.value,
                }))
              }
              placeholder="Paste new key"
            />

            <button onClick={() => saveKey(name)}>
              Save
            </button>

            <button
              onClick={() => testKey(name)}
              disabled={testing === name || !data.keys[name]}
            >
              Test
            </button>

            <button onClick={() => removeKey(name)}>
              Remove
            </button>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-title">
          <Settings size={18} />
          Providers
        </div>

        <div className="provider-tests">
          {["groq", "gemini", "ollama"].map(
            (provider) => (
              <button
                key={provider}
                onClick={() => test(provider)}
                disabled={testing === provider}
              >
                Test {provider}
                <span>
                  {data.models[provider]}
                </span>
              </button>
            ),
          )}
        </div>

        <label>
          Priority order
          <select
            value={data.provider_priority.join(",")}
            onChange={(event) =>
              savePriority(
                event.target.value.split(","),
              )
            }
          >
            <option value="groq,gemini,ollama">
              Groq → Gemini → Ollama
            </option>
            <option value="gemini,groq,ollama">
              Gemini → Groq → Ollama
            </option>
            <option value="ollama,groq,gemini">
              Ollama → Groq → Gemini
            </option>
          </select>
        </label>

        {notice && (
          <div className="notice">{notice}</div>
        )}
      </div>

      <div className="card">
        <div className="card-title">
          Local usage counters
        </div>
        <pre>
          {JSON.stringify(data.usage, null, 2)}
        </pre>
      </div>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState("agent");
  const [runId, setRunId] = useState(null);
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");
  const [steps, setSteps] = useState([]);
  const [screenshot, setScreenshot] = useState(null);
  const [viewport, setViewport] = useState({
    width: 1440,
    height: 900,
  });
  const [takeover, setTakeover] = useState(false);
  const [userActionReason, setUserActionReason] = useState("");
  const [historyTick, setHistoryTick] = useState(0);

  useEffect(() => {
    if (!runId) return undefined;

    const socket = new WebSocket(
      `${WS}/ws/runs/${runId}`,
    );

    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === "snapshot") {
        setStatus(data.status || "starting");
        setMessage(data.message || "");
        setTakeover(Boolean(data.takeover));
        setUserActionReason(data.user_action_reason || "");
        if (data.steps) {
          setSteps(data.steps);
        }
      }

      if (data.type === "status") {
        setStatus(data.status);
        setMessage(data.message || "");
        setTakeover(Boolean(data.takeover));

        if (
          ["completed", "failed", "stopped"].includes(
            data.status,
          )
        ) {
          setHistoryTick((value) => value + 1);
        }
      }

      if (data.type === "step") {
        setSteps((old) => [...old, data]);
      }

      if (data.type === "screenshot") {
        setScreenshot(data.image);
        setViewport({
          width: data.width || 1440,
          height: data.height || 900,
        });
      }

      if (data.type === "needs_user_action") {
        setUserActionReason(data.reason || "");
        setMessage(
          `User action required: ${data.reason}`,
        );
      }
    };

    return () => socket.close();
  }, [runId]);

  const run = async (body) => {
    setSteps([]);
    setScreenshot(null);
    setMessage("Starting browser…");
    setStatus("starting");
    setTakeover(false);
    setUserActionReason("");
    setTab("agent");

    try {
      const result = await api("/api/runs", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setRunId(result.id);
    } catch (error) {
      setStatus("failed");
      setMessage(error.message);
    }
  };

  const openHistory = async (id) => {
    const data = await api(
      `/api/history/${id}`,
    );

    setRunId(null);
    setStatus(data.status);
    setMessage(data.result || "");
    setSteps(data.steps || []);
    setScreenshot(null);
    setTakeover(false);
    setTab("agent");
  };

  const busy = [
    "starting",
    "running",
    "paused",
    "stopping",
  ].includes(status);

  return (
    <div className="app-shell">
      <header>
        <div className="brand">
          <div className="brand-mark">CA</div>
          <div>
            <strong>Chrome Agent</strong>
            <small>
              Local autonomous browser controller
            </small>
          </div>
        </div>

        <nav>
          <button
            className={
              tab === "agent" ? "active" : ""
            }
            onClick={() => setTab("agent")}
          >
            <Bot size={16} />
            Agent
          </button>

          <button
            className={
              tab === "history" ? "active" : ""
            }
            onClick={() => setTab("history")}
          >
            <History size={16} />
            History
          </button>

          <button
            className={
              tab === "settings" ? "active" : ""
            }
            onClick={() => setTab("settings")}
          >
            <Settings size={16} />
            Settings
          </button>
        </nav>
      </header>

      <main>
        {tab === "agent" && (
          <>
            <section className="top-grid">
              <TaskPanel
                onRun={run}
                busy={busy}
              />

              <div className="card run-card">
                <div className="card-title">
                  <ShieldAlert size={18} />
                  Current Run
                  <StatusBadge status={status} />
                </div>

                <div className="run-id">
                  {runId
                    ? `Run ${runId}`
                    : "No active run"}
                </div>

                <p>
                  {message ||
                    "Start a task to launch Chromium."}
                </p>

                <Controls
                  runId={runId}
                  status={status}
                  takeover={takeover}
                  userActionReason={userActionReason}
                  onChange={(name) => {
                    if (name === "takeover") {
                      setTakeover(true);
                    }
                    if (name === "resume") {
                      setTakeover(false);
                      setUserActionReason("");
                    }
                  }}
                />
              </div>
            </section>

            <section className="workspace">
              <BrowserView
                runId={runId}
                screenshot={screenshot}
                takeover={takeover}
                viewport={viewport}
              />

              <Logs
                steps={steps}
                status={status}
                message={message}
              />
            </section>
          </>
        )}

        {tab === "history" && (
          <HistoryPanel
            onOpen={openHistory}
            refreshToken={historyTick}
          />
        )}

        {tab === "settings" && (
          <SettingsPanel />
        )}
      </main>
    </div>
  );
}
