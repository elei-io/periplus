import assert from "node:assert/strict"
import { test } from "node:test"
import { parameterDraft, parameterValue, pasteParameters } from "../src/lib/query-parameters.ts"

test("pasted values infer types while preserving quoted text and large identifiers", () => {
  assert.deepEqual(pasteParameters('hello,42,true,null,"42",001,9007199254740993').map(parameterValue), ["hello", 42, true, null, "42", "001", "9007199254740993"])
})
test("CSV quotes preserve separators, escaped quotes, empty strings and newlines", () => {
  assert.deepEqual(pasteParameters('"a,b", "say ""hello"""\r\n"line\nbreak",,""\r\n').map(parameterValue), ["a,b", 'say "hello"', "line\nbreak", "", ""])
  assert.throws(() => pasteParameters('"unfinished'), /Close/)
  assert.throws(() => pasteParameters('"closed"oops'), /Separate/)
  assert.deepEqual(pasteParameters(""), [])
})
test("structured values preserve existing parameter types and validate numeric edits", () => {
  const values = ["", "42", 42, false, null, [1, 2], { a: true }]
  assert.deepEqual(values.map(parameterDraft).map(parameterValue), values)
  assert.throws(() => parameterValue({ kind: "number", value: "" }), /finite/)
  assert.throws(() => parameterValue({ kind: "number", value: "9007199254740993" }), /safe/)
  assert.throws(() => parameterValue({ kind: "json", value: "{" }), /valid JSON/)
})
