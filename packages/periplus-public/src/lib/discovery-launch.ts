/** Consume the launch intent before starting work, including under Strict Mode. */
export function consumeDiscoveryLaunch() {
  const url = new URL(window.location.href)
  if (url.searchParams.get("run") !== "1") return false
  url.searchParams.delete("run")
  window.history.replaceState(window.history.state, "", url)
  return true
}
