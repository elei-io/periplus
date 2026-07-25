import assert from "node:assert/strict";
import test from "node:test";
import {
  GhostTextEditor,
  type InteractiveTerminal,
} from "./editor.js";

class FakeTerminal implements InteractiveTerminal {
  output = "";
  width = 80;
  private readonly listeners = new Set<(data: string) => void>();

  writeRaw(value: string): void {
    this.output += value;
  }

  columns(): number {
    return this.width;
  }

  onData(listener: (data: string) => void) {
    this.listeners.add(listener);
    return { dispose: () => this.listeners.delete(listener) };
  }

  send(data: string): void {
    for (const listener of [...this.listeners]) listener(data);
  }
}

test("the shared editor displays and accepts ghost-text completion", async () => {
  const terminal = new FakeTerminal();
  const editor = new GhostTextEditor(
    terminal,
    async (input, cursor) => [{
      insertText: ".graphs",
      replaceStart: 0,
      replaceEnd: cursor,
      kind: "resource",
      description: "Crawl graphs",
    }],
    0,
  );
  const line = editor.readLine("atlas> ");
  terminal.send(".gra");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.match(terminal.output, /\u001b\[2mphs\u001b\[0m/);
  assert.match(terminal.output, /· Crawl graphs/);
  terminal.send("\t");
  terminal.send("\r");
  assert.equal(await line, ".graphs");
});

test("completion descriptions are bounded to one terminal line", async () => {
  const terminal = new FakeTerminal();
  terminal.width = 48;
  const description = "A".repeat(200);
  const editor = new GhostTextEditor(
    terminal,
    async (_input, cursor) => [{
      insertText: "suggest_json_ld_schemas(",
      replaceStart: 0,
      replaceEnd: cursor,
      kind: "function",
      description,
    }],
    0,
  );
  const line = editor.readLine("atlas> ");
  terminal.send("suggest");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.doesNotMatch(terminal.output, new RegExp(description));
  assert.match(terminal.output, /…/);
  terminal.send("\u0003");
  await line;
});

test("submitting a line erases ghost text before committing it to scrollback", async () => {
  const terminal = new FakeTerminal();
  const editor = new GhostTextEditor(
    terminal,
    async (_input, cursor) => [{
      insertText: "SELECT; FROM macros",
      replaceStart: 0,
      replaceEnd: cursor,
      kind: "relation",
      description: "Macro schema",
    }],
    0,
  );
  const line = editor.readLine("atlas> ");
  terminal.send("SELECT;");
  await new Promise((resolve) => setTimeout(resolve, 5));
  terminal.send("\r");

  assert.equal(await line, "SELECT;");
  assert.match(
    terminal.output,
    /\r\u001b\[2K\u001b\[1;34matlas> \u001b\[0mSELECT;(?:\u001b\[2m\u001b\[0m)+\r\n$/,
  );
});

test("starting a continuation erases ghost text from the previous line", async () => {
  const terminal = new FakeTerminal();
  const editor = new GhostTextEditor(
    terminal,
    async (_input, cursor) => [{
      insertText: "SELECT * FROM macros",
      replaceStart: 0,
      replaceEnd: cursor,
      kind: "relation",
      description: "Macro schema",
    }],
    0,
  );
  const line = editor.readLine("atlas> ");
  terminal.send("SELECT");
  await new Promise((resolve) => setTimeout(resolve, 5));
  terminal.send("\r");

  assert.match(
    terminal.output,
    /\r\u001b\[2K\u001b\[1;34matlas> \u001b\[0mSELECT(?:\u001b\[2m\u001b\[0m)+\r\n/,
  );
  terminal.send("  1;");
  terminal.send("\r");
  assert.equal(await line, "SELECT\n  1;");
});

test("SQL input continues across lines until a semicolon", async () => {
  const terminal = new FakeTerminal();
  const history: string[] = [];
  const editor = new GhostTextEditor(
    terminal,
    async () => [],
    0,
    history,
  );
  const line = editor.readLine("atlas> ");
  terminal.send("select\r  42;\r");
  assert.equal(await line, "select\n  42;");
  assert.deepEqual(history, ["select\n  42;"]);
  assert.match(terminal.output, /\.\.\.> /);
});

test("the editor reports input changes without owning their presentation", async () => {
  const terminal = new FakeTerminal();
  const inputs: string[] = [];
  const editor = new GhostTextEditor(
    terminal,
    async () => [],
    0,
    [],
    (input) => inputs.push(input),
  );
  const line = editor.readLine("atlas> ");
  terminal.send("SELECT 1;");
  terminal.send("\r");
  assert.equal(await line, "SELECT 1;");
  assert.ok(inputs.includes("SELECT 1;"));
  assert.equal(inputs.at(-1), "");
});

test("the editor can begin with adapter-provided input", async () => {
  const terminal = new FakeTerminal();
  const editor = new GhostTextEditor(terminal, async () => [], 0);
  const line = editor.readLine("atlas> ", "SELECT 42;");
  terminal.send("\r");

  assert.equal(await line, "SELECT 42;");
  assert.match(terminal.output, /SELECT 42;/);
});
