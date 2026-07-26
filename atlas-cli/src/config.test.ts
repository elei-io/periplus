import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolveApiUrl, resolveWebUrl } from "./config.js";

test("the API URL defaults to local development", async () => {
  const directory = await mkdtemp(join(tmpdir(), "atlas-cli-"));
  assert.equal(
    await resolveApiUrl(undefined, directory),
    "http://127.0.0.1:8000",
  );
  assert.equal(
    await resolveWebUrl(undefined, directory),
    "http://127.0.0.1:8080",
  );
});

test("a project atlas.json overrides the local default", async () => {
  const directory = await mkdtemp(join(tmpdir(), "atlas-cli-"));
  await writeFile(
    join(directory, "atlas.json"),
    JSON.stringify({
      api_url: "https://api.atlas.example.com/",
      web_url: "https://atlas.example.com/",
    }),
  );
  assert.equal(
    await resolveApiUrl(undefined, directory),
    "https://api.atlas.example.com",
  );
  assert.equal(
    await resolveWebUrl(undefined, directory),
    "https://atlas.example.com",
  );
});
