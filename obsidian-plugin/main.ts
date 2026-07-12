import { ItemView, MarkdownRenderer, Notice, Plugin, PluginSettingTab, Setting, WorkspaceLeaf } from "obsidian";
import { shell } from "electron";
import { spawn } from "child_process";
import { existsSync, promises as fs } from "fs";
import * as path from "path";

const VIEW_TYPE = "semantic-traversal-view";

interface SemanticTraversalSettings {
  pythonExecutable: string;
  runtimeRoot: string;
  configPath: string;
  artifactRoot: string;
  lastThreadId: string;
  threadScrollPositions: Record<string, number>;
}

const DEFAULT_SETTINGS: SemanticTraversalSettings = {
  pythonExecutable: "python",
  runtimeRoot: "",
  configPath: "",
  artifactRoot: "",
  lastThreadId: "",
  threadScrollPositions: {},
};

interface ThreadDocument {
  thread_id: string;
  updated_at?: string;
  latest_turn_id?: number;
  messages?: Array<{ role: string; content: string; turn_id?: number }>;
}

interface TurnResult {
  thread_id: string;
  turn_id: number;
  assistant_response?: string | null;
  runtime_outcome: string;
  blocking_reasons?: string[];
  turn_root?: string;
  conversation_thread_path?: string;
  coverage_decision?: string;
  semantic_compiler_status?: string;
}

interface PersistedTurnSummary extends TurnResult {
  llmCallMetadata?: {
    synthesis_status?: string;
    provider?: string | null;
    model?: string | null;
    response_id?: string | null;
    reasoning_effort?: string | null;
    usage?: Record<string, unknown> | null;
    error?: string | null;
  };
}

interface IngestResult {
  status: string;
  note_count?: number;
  chunk_count?: number;
}

export default class SemanticTraversalPlugin extends Plugin {
  settings: SemanticTraversalSettings = DEFAULT_SETTINGS;
  activeThreadId: string | null = null;
  view: SemanticTraversalView | null = null;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.registerView(VIEW_TYPE, (leaf) => {
      const view = new SemanticTraversalView(leaf, this);
      this.view = view;
      return view;
    });
    this.addRibbonIcon("message-circle", "Open semantic traversal", () => this.openView());
    this.addCommand({ id: "open-view", name: "Open semantic traversal", callback: () => this.openView() });
    this.addCommand({ id: "ingest-vault", name: "Ingest vault for semantic traversal", callback: () => this.ingest() });
    this.addSettingTab(new SemanticTraversalSettingTab(this.app, this));
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    this.settings.threadScrollPositions ??= {};
    this.activeThreadId = this.settings.lastThreadId || null;
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  async openView(): Promise<void> {
    const existing = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    const leaf = existing ?? this.app.workspace.getRightLeaf(false);
    if (!leaf) {
      new Notice("Could not open the semantic traversal view.");
      return;
    }
    await leaf.setViewState({ type: VIEW_TYPE, active: true });
    this.app.workspace.revealLeaf(leaf);
  }

  async ingest(): Promise<void> {
    try {
      const result = await this.runCli(["ingest"]);
      const payload = this.parseJson<IngestResult>(result.stdout);
      new Notice(payload ? `Ingestion completed: ${payload.note_count ?? 0} notes, ${payload.chunk_count ?? 0} chunks.` : "Ingestion completed.");
    } catch (error) {
      new Notice(`Ingestion failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async runTurn(message: string, threadId: string | null): Promise<TurnResult> {
    const args = ["--message", message];
    if (threadId) args.push("--thread-id", threadId);
    const result = await this.runCli(args, true);
    const payload = this.parseJson<TurnResult>(result.stdout);
    if (!payload) {
      throw new Error(result.stderr || "The runtime did not return JSON.");
    }
    return payload;
  }

  private async runCli(args: string[], allowBlocked = false): Promise<{ stdout: string; stderr: string }> {
    const runtimeRoot = this.settings.runtimeRoot.trim();
    const configPath = this.settings.configPath.trim();
    if (!runtimeRoot || !configPath) throw new Error("Configure the runtime root and YAML config path first.");
    const cliArgs = ["-m", "semantic_traversal", ...args, "--repo-root", runtimeRoot, "--config", configPath];
    const result = await new Promise<{ stdout: string; stderr: string; code: number | null }>((resolve, reject) => {
      const child = spawn(this.settings.pythonExecutable.trim() || "python", cliArgs, { cwd: runtimeRoot, windowsHide: true });
      let stdout = "";
      let stderr = "";
      child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
      child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });
      child.on("error", reject);
      child.on("close", (code) => resolve({ stdout, stderr, code }));
    });
    if (result.code !== 0 && !allowBlocked) throw new Error(result.stderr || "The CLI process failed.");
    return result;
  }

  private parseJson<T>(stdout: string): T | null {
    try { return JSON.parse(stdout) as T; } catch { return null; }
  }

  getArtifactRoot(): string {
    return this.settings.artifactRoot.trim();
  }

  async listThreads(): Promise<string[]> {
    const artifactRoot = this.getArtifactRoot();
    if (!artifactRoot) return [];
    const root = path.join(artifactRoot, "threads");
    if (!existsSync(root)) return [];
    const entries = await fs.readdir(root, { withFileTypes: true });
    return entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort().reverse();
  }

  async readThread(threadId: string): Promise<ThreadDocument | null> {
    try {
      const filePath = path.join(this.getArtifactRoot(), "threads", threadId, "conversation_thread.json");
      return JSON.parse(await fs.readFile(filePath, "utf8")) as ThreadDocument;
    } catch {
      return null;
    }
  }

  async readThreadTurnSummaries(threadId: string): Promise<PersistedTurnSummary[]> {
    const threadRoot = path.join(this.getArtifactRoot(), "threads", threadId);
    const turnsRoot = path.join(threadRoot, "turns");
    if (!existsSync(turnsRoot)) return [];
    const ledgerByTurn = new Map<number, PersistedTurnSummary["llmCallMetadata"]>();
    try {
      const ledgerText = await fs.readFile(path.join(threadRoot, "thread_ledger.jsonl"), "utf8");
      for (const line of ledgerText.split(/\r?\n/)) {
        if (!line.trim()) continue;
        const record = JSON.parse(line) as { turn_id?: number; llm_call_metadata?: PersistedTurnSummary["llmCallMetadata"] };
        if (typeof record.turn_id === "number") ledgerByTurn.set(record.turn_id, record.llm_call_metadata);
      }
    } catch {
      // Threads created before ledger telemetry remain renderable.
    }
    const entries = await fs.readdir(turnsRoot, { withFileTypes: true });
    const summaries: Array<PersistedTurnSummary | null> = await Promise.all(entries.filter((entry) => entry.isDirectory()).map(async (entry): Promise<PersistedTurnSummary | null> => {
      try {
        const stateDelta = JSON.parse(await fs.readFile(path.join(turnsRoot, entry.name, "state_delta.json"), "utf8")) as {
          turn_id?: number;
          runtime_outcome?: string;
          blocking_reasons?: string[];
          semantic_compiler_status?: string;
          coverage_decision?: string;
        };
        if (typeof stateDelta.turn_id !== "number") return null;
        return {
          thread_id: threadId,
          turn_id: stateDelta.turn_id,
          runtime_outcome: stateDelta.runtime_outcome ?? "unknown",
          blocking_reasons: stateDelta.blocking_reasons ?? [],
          semantic_compiler_status: stateDelta.semantic_compiler_status,
          coverage_decision: stateDelta.coverage_decision,
          turn_root: path.join(turnsRoot, entry.name),
          llmCallMetadata: ledgerByTurn.get(stateDelta.turn_id),
        };
      } catch {
        return null;
      }
    }));
    return summaries.filter((summary): summary is PersistedTurnSummary => summary !== null).sort((left, right) => left.turn_id - right.turn_id);
  }

  async listThreadSummaries(): Promise<Array<{ threadId: string; label: string }>> {
    const threadIds = await this.listThreads();
    return Promise.all(threadIds.map(async (threadId) => {
      const document = await this.readThread(threadId);
      const firstUserMessage = document?.messages?.find((message) => message.role === "user")?.content;
      return { threadId, label: this.threadLabel(firstUserMessage, threadId) };
    }));
  }

  private threadLabel(firstUserMessage: string | undefined, threadId: string): string {
    const normalized = firstUserMessage?.replace(/\s+/g, " ").trim();
    if (!normalized) return `Untitled thread · ${threadId.slice(0, 8)}`;
    return normalized.length > 54 ? `${normalized.slice(0, 51).trimEnd()}…` : normalized;
  }
}

class SemanticTraversalView extends ItemView {
  private readonly plugin: SemanticTraversalPlugin;
  private messagesEl!: HTMLElement;
  private threadSelect!: HTMLSelectElement;
  private input!: HTMLTextAreaElement;
  private statusEl!: HTMLElement;
  private inspectorEl!: HTMLElement;
  private scrollPersistTimer: number | null = null;

  constructor(leaf: WorkspaceLeaf, plugin: SemanticTraversalPlugin) {
    super(leaf);
    this.plugin = plugin;
  }

  getViewType(): string { return VIEW_TYPE; }
  getDisplayText(): string { return "Semantic traversal"; }
  getIcon(): string { return "message-circle"; }

  async onOpen(): Promise<void> {
    this.renderShell();
    await this.renderThreadList();
  }

  async onClose(): Promise<void> {
    await this.persistActiveScrollPosition();
    this.contentEl.empty();
  }

  private renderShell(): void {
    this.contentEl.empty();
    const root = this.contentEl.createDiv({ cls: "semantic-traversal-view" });
    const header = root.createDiv({ cls: "semantic-traversal-header" });
    header.createDiv({ text: "Semantic traversal", cls: "semantic-traversal-title" });
    this.threadSelect = header.createEl("select");
    this.threadSelect.addEventListener("change", () => void this.selectThread(this.threadSelect.value));
    const newButton = header.createEl("button", { text: "New thread" });
    newButton.addEventListener("click", () => void this.newThread());
    const ingestButton = header.createEl("button", { text: "Ingest" });
    ingestButton.addEventListener("click", () => void this.plugin.ingest());

    this.messagesEl = root.createDiv({ cls: "semantic-traversal-messages" });
    this.messagesEl.addEventListener("scroll", () => this.queueScrollPositionSave());
    this.inspectorEl = root.createDiv({ cls: "semantic-traversal-inspector" });
    this.inspectorEl.createDiv({ text: "Select a turn to inspect its runtime details." });

    const composer = root.createDiv({ cls: "semantic-traversal-composer" });
    this.input = composer.createEl("textarea", { cls: "semantic-traversal-input", attr: { placeholder: "Ask about your vault…" } });
    this.input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.isComposing || event.shiftKey) return;
      event.preventDefault();
      void this.submit();
    });
    const sendButton = composer.createEl("button", { text: "Send" });
    sendButton.addEventListener("click", () => void this.submit());
    this.statusEl = composer.createDiv({ cls: "semantic-traversal-status", attr: { "aria-live": "polite" } });
    this.statusEl.setText("Enter to send · Shift+Enter for a new line · Ctrl/Cmd+Enter to send");
  }

  private async renderThreadList(selectActiveThread = true): Promise<void> {
    const threads = await this.plugin.listThreadSummaries();
    this.threadSelect.empty();
    this.threadSelect.createEl("option", { text: threads.length ? "Select a thread" : "No threads yet", value: "" });
    for (const thread of threads) {
      const option = this.threadSelect.createEl("option", { text: thread.label, value: thread.threadId });
      option.title = thread.threadId;
    }
    const selected = this.plugin.activeThreadId && threads.some((thread) => thread.threadId === this.plugin.activeThreadId)
      ? this.plugin.activeThreadId
      : threads[0]?.threadId;
    if (selected && selectActiveThread) await this.selectThread(selected);
  }

  private async selectThread(threadId: string): Promise<void> {
    if (!threadId) return;
    if (this.plugin.activeThreadId && this.plugin.activeThreadId !== threadId) {
      await this.persistActiveScrollPosition();
    }
    this.plugin.activeThreadId = threadId;
    this.plugin.settings.lastThreadId = threadId;
    await this.plugin.saveSettings();
    this.threadSelect.value = threadId;
    const document = await this.plugin.readThread(threadId);
    this.messagesEl.empty();
    if (!document) {
      this.statusEl.setText("Could not render the selected thread");
      return;
    }
    const turnSummaries = await this.plugin.readThreadTurnSummaries(threadId);
    const summaryByTurnId = new Map(turnSummaries.map((summary) => [summary.turn_id, summary]));
    for (const message of document.messages ?? []) {
      await this.renderMessage(message.role, message.content);
      if (message.role === "assistant" && typeof message.turn_id === "number") {
        const summary = summaryByTurnId.get(message.turn_id);
        if (summary && summary.turn_id === document.latest_turn_id) this.renderInspector(summary);
      }
    }
    this.statusEl.setText("Enter to send · Shift+Enter for a new line · Ctrl/Cmd+Enter to send");
    const savedScrollPosition = this.plugin.settings.threadScrollPositions[threadId];
    this.messagesEl.scrollTop = savedScrollPosition ?? this.messagesEl.scrollHeight;
  }

  private async renderMessage(role: string, content: string): Promise<HTMLElement> {
    const messageEl = this.messagesEl.createDiv({ cls: `semantic-traversal-message semantic-traversal-message-${role}` });
    messageEl.createDiv({ text: role === "user" ? "You" : "Agent", cls: "semantic-traversal-message-label" });
    const contentEl = messageEl.createDiv({ cls: "semantic-traversal-message-content" });
    if (role === "assistant") {
      await MarkdownRenderer.renderMarkdown(content, contentEl, "", this);
    } else {
      contentEl.setText(content);
    }
    return messageEl;
  }

  private async submit(): Promise<void> {
    const message = this.input.value.trim();
    if (!message) return;
    this.input.disabled = true;
    await this.renderMessage("user", message);
    const loadingEl = this.messagesEl.createDiv({ cls: "semantic-traversal-message semantic-traversal-message-preparation" });
    loadingEl.createDiv({ text: "Assistant", cls: "semantic-traversal-message-label" });
    loadingEl.createDiv({ text: "Preparing context for the Agent…", cls: "semantic-traversal-message-content" });
    this.scrollToElement(loadingEl);
    this.statusEl.setText("Assistant is preparing context for the Agent…");
    try {
      const result = await this.plugin.runTurn(message, this.plugin.activeThreadId);
      const previousThreadId = this.plugin.activeThreadId;
      if (previousThreadId && previousThreadId !== result.thread_id) await this.persistActiveScrollPosition();
      this.plugin.activeThreadId = result.thread_id;
      this.input.value = "";
      await this.renderThreadList(false);
      await this.selectThread(result.thread_id);
      const latestSummary = (await this.plugin.readThreadTurnSummaries(result.thread_id)).find((summary) => summary.turn_id === result.turn_id);
      this.renderInspector(latestSummary ?? result);
      this.scrollToBottom();
    } catch (error) {
      this.statusEl.addClass("semantic-traversal-error");
      this.statusEl.setText(error instanceof Error ? error.message : String(error));
    } finally {
      this.input.disabled = false;
      this.input.focus();
    }
  }

  private async newThread(): Promise<void> {
    await this.persistActiveScrollPosition();
    this.plugin.activeThreadId = null;
    this.threadSelect.value = "";
    this.messagesEl.empty();
    this.inspectorEl.empty();
    this.inspectorEl.createDiv({ text: "New thread. Runtime will create an id when you send the first message." });
    this.statusEl.setText("New thread");
    this.input.focus();
  }

  private renderInspector(result: TurnResult): void {
    this.inspectorEl.empty();
    const details = this.inspectorEl.createEl("details");
    const coverage = result.coverage_decision ?? "unknown coverage";
    details.createEl("summary", { text: `Turn ${result.turn_id} · ${result.runtime_outcome} · ${coverage}` });
    const detailGrid = details.createDiv({ cls: "semantic-traversal-inspector-details" });
    detailGrid.createEl("div", { text: `Assistant preparation: ${result.semantic_compiler_status ?? "unknown"}` });
    detailGrid.createEl("div", { text: `Retrieval coverage: ${coverage}` });
    const persisted = result as PersistedTurnSummary;
    const synthesisStatus = persisted.llmCallMetadata?.synthesis_status ?? (result.runtime_outcome === "completed" ? "completed" : "not attempted");
    detailGrid.createEl("div", { text: `Agent synthesis: ${synthesisStatus}` });
    if (persisted.llmCallMetadata?.model) detailGrid.createEl("div", { text: `Agent model: ${persisted.llmCallMetadata.model}` });
    if (persisted.llmCallMetadata?.reasoning_effort) detailGrid.createEl("div", { text: `Reasoning effort: ${persisted.llmCallMetadata.reasoning_effort}` });
    if (persisted.llmCallMetadata?.usage) detailGrid.createEl("div", { text: `Usage telemetry: ${JSON.stringify(persisted.llmCallMetadata.usage)}` });
    if (persisted.llmCallMetadata?.error) detailGrid.createEl("div", { text: `Agent error: ${persisted.llmCallMetadata.error}` });
    if (result.blocking_reasons?.length) detailGrid.createEl("div", { text: `Blocking reasons: ${result.blocking_reasons.join(" ")}` });
    if (result.turn_root) {
      const artifact = detailGrid.createDiv({ cls: "semantic-traversal-artifact-path" });
      artifact.createEl("span", { text: "Turn artifacts: " });
      const openButton = artifact.createEl("button", { text: result.turn_root, cls: "semantic-traversal-artifact-button" });
      openButton.title = "Open this turn's artifact folder in the file explorer";
      openButton.addEventListener("click", () => void this.openArtifactFolder(result.turn_root!));
    }
  }

  private async openArtifactFolder(folderPath: string): Promise<void> {
    const error = await shell.openPath(folderPath);
    if (error) new Notice(`Could not open artifact folder: ${error}`);
  }

  private scrollToElement(element: HTMLElement): void {
    element.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  private scrollToBottom(): void {
    this.messagesEl.scrollTo({ top: this.messagesEl.scrollHeight, behavior: "smooth" });
  }

  private queueScrollPositionSave(): void {
    if (this.scrollPersistTimer !== null) window.clearTimeout(this.scrollPersistTimer);
    this.scrollPersistTimer = window.setTimeout(() => {
      this.scrollPersistTimer = null;
      void this.persistActiveScrollPosition();
    }, 250);
  }

  private async persistActiveScrollPosition(): Promise<void> {
    const threadId = this.plugin.activeThreadId;
    if (!threadId || !this.messagesEl) return;
    this.plugin.settings.threadScrollPositions[threadId] = this.messagesEl.scrollTop;
    await this.plugin.saveSettings();
  }
}

class SemanticTraversalSettingTab extends PluginSettingTab {
  private readonly plugin: SemanticTraversalPlugin;

  constructor(app: SemanticTraversalPlugin["app"], plugin: SemanticTraversalPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("p", { text: "The plugin renders persisted threads and shells out to the external Python runtime. These settings do not replace the runtime YAML." });
    new Setting(containerEl).setName("Python executable").setDesc("Executable used to run the CLI.").addText((text) => text.setValue(this.plugin.settings.pythonExecutable).onChange(async (value) => { this.plugin.settings.pythonExecutable = value.trim() || "python"; await this.plugin.saveSettings(); }));
    new Setting(containerEl).setName("Runtime root").setDesc("Directory containing the Python package; also used as the CLI working directory.").addText((text) => text.setValue(this.plugin.settings.runtimeRoot).onChange(async (value) => { this.plugin.settings.runtimeRoot = value.trim(); await this.plugin.saveSettings(); }));
    new Setting(containerEl).setName("Runtime config path").setDesc("External semantic_traversal.runtime.yaml path.").addText((text) => text.setValue(this.plugin.settings.configPath).onChange(async (value) => { this.plugin.settings.configPath = value.trim(); await this.plugin.saveSettings(); }));
    new Setting(containerEl).setName("Thread artifact root").setDesc("The data root configured by the YAML, for example <vault>/.semantic_traversal.").addText((text) => text.setValue(this.plugin.settings.artifactRoot).onChange(async (value) => { this.plugin.settings.artifactRoot = value.trim(); await this.plugin.saveSettings(); }));
  }
}
