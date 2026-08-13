import assert from "node:assert/strict"
import test from "node:test"

import { WELCOME } from "./welcome.js"

test("welcome banner renders the Periplus wordmark", () => {
  assert.equal(
    WELCOME.split("\n").slice(0, 5).join("\n"),
    [
      " ____  _____ ____  ___ ____  _    _   _ ____  ",
      "|  _ \\| ____|  _ \\|_ _|  _ \\| |  | | | / ___| ",
      "| |_) |  _| | |_) || || |_) | |  | | | \\___ \\ ",
      "|  __/| |___|  _ < | ||  __/| |__| |_| |___) |",
      "|_|   |_____|_| \\_\\___|_|   |_____\\___/|____/ ",
    ].join("\n"),
  )
  assert.match(WELCOME, /query the web with SQL/)
})
