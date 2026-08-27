import assert from "node:assert/strict"
import test from "node:test"

import {
  contentTypeGroupState,
  parseContentTypes,
  setContentTypeGroup,
} from "./content-types.ts"

test("content groups add their MIME types without removing custom values", () => {
  const result = setContentTypeGroup(
    "text/html, application/x-custom",
    "word",
    true
  )
  assert.deepEqual(parseContentTypes(result), [
    "text/html",
    "application/x-custom",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
  ])
  assert.equal(contentTypeGroupState(result, "word"), "on")
})

test("turning a group off removes only MIME types owned by that group", () => {
  const result = setContentTypeGroup(
    "text/html, image/*, application/x-custom",
    "images",
    false
  )
  assert.deepEqual(parseContentTypes(result), [
    "text/html",
    "application/x-custom",
  ])
})

test("partially configured groups remain visible without being expanded", () => {
  const value = "text/html, application/msword"
  assert.equal(contentTypeGroupState(value, "word"), "partial")
  assert.deepEqual(parseContentTypes(value), [
    "text/html",
    "application/msword",
  ])
})
