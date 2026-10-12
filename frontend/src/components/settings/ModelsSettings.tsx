import { useId, useState } from "react";

import { friendlyError } from "../../api/client";
import type { AiConnection, AiTask, AiTaskName, AiTest } from "../../api/rules";
import { useAiConnections, useAiTasks } from "../../api/rules";
import { useDeleteConnection, useListModels, useSaveConnection, useSaveTask, useTestModel } from "../../hooks/useRules";
import { Button, Dialog, Field, inputClass, Notice } from "../ui";

const TASK_TITLES: Record<AiTaskName, string> = { sorting: "Sorting", writing_rules: "Writing rules" };
const TASK_ORDER: AiTaskName[] = ["sorting", "writing_rules"];

const tested = (t: AiTest) =>
  t.ok ? `Answered in ${(t.took_ms / 1000).toFixed(1)} s · picked ${t.chose ?? "none"} · answer mode ${t.mode}` : (t.error ?? "It didn't work.");

export function ModelsSettings() {
  const connections = useAiConnections().data ?? [];
  const tasks = useAiTasks().data;
  return (
    <div className="space-y-8">
      <p className="text-table text-ink-2">
        CleanSync talks to any OpenAI-style server: Ollama, LM Studio, llama.cpp, vLLM, a LiteLLM gateway, OpenRouter or Groq. The address usually ends in /v1.
      </p>
      <Connections connections={connections} />
      <section className="space-y-4">
        <h2 className="text-section font-semibold">Models for each task</h2>
        {tasks &&
          TASK_ORDER.map((name) => (
            <TaskBlock
              key={name}
              name={name}
              task={tasks[name] ?? { task: name, connection_id: null, model: "", temperature: 0, timeout_s: 60 }}
              connections={connections}
            />
          ))}
      </section>
    </div>
  );
}

function Connections({ connections }: { connections: AiConnection[] }) {
  const [editing, setEditing] = useState<AiConnection | "new" | null>(null);
  const remove = useDeleteConnection();
  return (
    <section className="space-y-3">
      <h2 className="text-section font-semibold">Connections</h2>
      {connections.length === 0 && <p className="text-table text-ink-2">No connections yet.</p>}
      <ul className="space-y-2">
        {connections.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-2 rounded-card border border-line px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="font-medium">{c.name}</p>
              <p className="break-all text-table text-ink-2">{c.base_url}</p>
              {c.has_key && <p className="text-meta text-ink-2">API key saved</p>}
            </div>
            <Button tone="quiet" onClick={() => setEditing(c)} aria-label={`Edit ${c.name}`}>
              Edit
            </Button>
            <Button tone="quiet" disabled={remove.isPending} onClick={() => remove.mutate({ id: c.id })} aria-label={`Remove ${c.name}`}>
              Remove
            </Button>
          </li>
        ))}
      </ul>
      <Button onClick={() => setEditing("new")}>Add connection</Button>
      {editing && <ConnectionDialog existing={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </section>
  );
}

type HeaderRow = { name: string; value: string };

function ConnectionDialog({ existing, onClose }: { existing: AiConnection | null; onClose: () => void }) {
  const save = useSaveConnection();
  const [name, setName] = useState(existing?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? "");
  const [key, setKey] = useState("");
  const [dropKey, setDropKey] = useState(false);
  const [rows, setRows] = useState<HeaderRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const setRow = (i: number, patch: Partial<HeaderRow>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const submit = () => {
    const headers = Object.fromEntries(rows.filter((r) => r.name.trim() && r.value).map((r) => [r.name.trim(), r.value]));
    const connection: Parameters<typeof save.mutate>[0]["connection"] = { name: name.trim(), base_url: baseUrl.trim() };
    if (!existing) {
      connection.api_key = key || null;
      connection.headers = headers;
    } else {
      if (dropKey) connection.api_key = null;
      else if (key) connection.api_key = key;
      if (Object.keys(headers).length > 0) connection.headers = headers;
    }
    setError(null);
    save.mutate({ id: existing?.id, connection }, { onSuccess: onClose, onError: (e) => setError(friendlyError(e)) });
  };

  return (
    <Dialog
      title={existing ? "Edit connection" : "Add connection"}
      onClose={onClose}
      footer={
        <>
          <Button tone="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button tone="primary" disabled={!name.trim() || !baseUrl.trim() || save.isPending} onClick={submit}>
            Save connection
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Address" hint="Usually ends in /v1.">
          <input className={inputClass} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://ollama.example.com:11434/v1" />
        </Field>
        <Field label="API key (optional)" hint={existing?.has_key ? "Leave empty to keep the saved key" : undefined}>
          <input className={inputClass} type="password" autoComplete="off" value={key} disabled={dropKey} onChange={(e) => setKey(e.target.value)} />
        </Field>
        {existing?.has_key && (
          <label className="flex items-center gap-2 text-table">
            <input type="checkbox" checked={dropKey} onChange={(e) => setDropKey(e.target.checked)} />
            Remove saved key
          </label>
        )}
        <fieldset className="space-y-2">
          <legend className="mb-1 text-table font-medium text-ink-2">Extra headers (optional)</legend>
          {existing && existing.header_names.length > 0 && (
            <p className="text-meta text-ink-2">
              Saved headers: {existing.header_names.join(", ")}. Leave the rows empty to keep them. Filling in rows replaces all of them.
            </p>
          )}
          {rows.map((r, i) => (
            <div key={i} className="flex flex-wrap gap-2">
              <input aria-label={`Header name ${i + 1}`} className={`${inputClass} min-w-0 flex-1`} value={r.name} onChange={(e) => setRow(i, { name: e.target.value })} />
              <input
                aria-label={`Header value ${i + 1}`}
                type="password"
                autoComplete="off"
                className={`${inputClass} min-w-0 flex-1`}
                value={r.value}
                onChange={(e) => setRow(i, { value: e.target.value })}
              />
              <Button tone="quiet" onClick={() => setRows(rows.filter((_, j) => j !== i))} aria-label={`Remove header ${i + 1}`}>
                Remove
              </Button>
            </div>
          ))}
          <Button tone="quiet" onClick={() => setRows([...rows, { name: "", value: "" }])}>
            Add header
          </Button>
        </fieldset>
        {error && <Notice tone="err">{error}</Notice>}
      </div>
    </Dialog>
  );
}

function TaskBlock({ name, task, connections }: { name: AiTaskName; task: AiTask; connections: AiConnection[] }) {
  const listId = useId();
  const save = useSaveTask();
  const list = useListModels();
  const test = useTestModel();
  const [connectionId, setConnectionId] = useState<number | null>(task.connection_id);
  const [model, setModel] = useState(task.model);
  const [temperature, setTemperature] = useState(String(task.temperature));
  const [timeout, setTimeoutS] = useState(String(task.timeout_s));
  const [result, setResult] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  const fields = () => ({ connection_id: connectionId, model: model.trim(), temperature: Number(temperature) || 0, timeout_s: Number(timeout) || 60 });
  const show = (tone: "ok" | "err", text: string) => setResult({ tone, text });

  return (
    <fieldset role="group" aria-label={TASK_TITLES[name]} className="space-y-3 rounded-card border border-line p-4">
      <legend className="px-1 font-medium">{TASK_TITLES[name]}</legend>
      {name === "writing_rules" && <p className="text-meta text-ink-2">Uses the Sorting model when not set.</p>}
      <Field label="Connection">
        <select className={inputClass} value={connectionId ?? ""} onChange={(e) => setConnectionId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">None</option>
          {connections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </Field>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <Field label="Model">
            <input className={inputClass} list={listId} value={model} onChange={(e) => setModel(e.target.value)} />
          </Field>
        </div>
        <datalist id={listId}>{(list.data?.models ?? []).map((m) => <option key={m} value={m} />)}</datalist>
        <Button
          disabled={connectionId === null || list.isPending}
          onClick={() => {
            if (connectionId === null) return;
            setResult(null);
            list.mutate({ id: connectionId }, { onError: (e) => show("err", friendlyError(e)) });
          }}
        >
          List models
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Temperature">
          <input className={inputClass} type="number" min={0} max={2} step={0.1} value={temperature} onChange={(e) => setTemperature(e.target.value)} />
        </Field>
        <Field label="Timeout (seconds)">
          <input className={inputClass} type="number" min={1} step={1} value={timeout} onChange={(e) => setTimeoutS(e.target.value)} />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          tone="primary"
          disabled={save.isPending}
          onClick={() => {
            setResult(null);
            save.mutate({ task: name, fields: fields() }, { onSuccess: () => show("ok", "Saved."), onError: () => undefined });
          }}
        >
          Save
        </Button>
        <Button
          disabled={connectionId === null || !model.trim() || test.isPending}
          onClick={() => {
            if (connectionId === null) return;
            setResult(null);
            test.mutate(
              { ...fields(), connection_id: connectionId },
              { onSuccess: (t) => show(t.ok ? "ok" : "err", tested(t)), onError: (e) => show("err", friendlyError(e)) },
            );
          }}
        >
          {test.isPending ? "Testing…" : "Test"}
        </Button>
      </div>
      {result && <Notice tone={result.tone}>{result.text}</Notice>}
    </fieldset>
  );
}
