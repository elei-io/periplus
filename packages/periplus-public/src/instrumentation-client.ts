import posthog from "posthog-js"
import { navigationAnalytics } from "./lib/navigation-analytics"
import { analyticsUrl, redactAnalyticsProperties } from "./lib/analytics-privacy"

const projectToken = process.env.NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN
const apiHost = process.env.NEXT_PUBLIC_POSTHOG_HOST
const environment = process.env.NEXT_PUBLIC_ANALYTICS_ENVIRONMENT ?? "development"

if (projectToken && apiHost && environment === "production") {
  const internalTest = new URLSearchParams(window.location.search).get("analytics_test") === "1"
  // Named link interactions only; never collect link text or input-bearing URLs.
  const trackNavigation = (event: MouseEvent) => {
    if (event.type === "auxclick" && event.button !== 1) return
    const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null
    if (!(anchor instanceof HTMLAnchorElement)) return
    const navigation = navigationAnalytics(anchor.href, window.location.href)
    if (navigation) { try { posthog.capture(navigation.event, navigation.properties) } catch { /* Best effort. */ } }
  }
  document.addEventListener("click", trackNavigation)
  document.addEventListener("auxclick", trackNavigation)
  posthog.init(projectToken, {
    api_host: apiHost,
    loaded: client => {
      if (internalTest) {
        client.register({ $internal_or_test_user: true })
        client.setPersonProperties({ $internal_or_test_user: true })
      }
    },
    defaults: "2026-01-30",
    capture_pageview: "history_change",
    capture_exceptions: true,
    autocapture: false,
    capture_dead_clicks: false,
    capture_heatmaps: false,
    enable_recording_console_log: false,
    disable_surveys: true,
    disable_session_recording: environment !== "production",
    session_recording: {
      sampleRate: 0.2,
      maskAllInputs: true,
      blockSelector: ".sql-workbench, .discovery-workspace, .schema-editor, .dataset-surface, table, pre, .observatory-request-story, a[href*=\"question=\"], a[href*=\"draft=\"], a[href*=\"sql=\"], a[href*=\"description=\"]",
      maskTextSelector: ".sql-workbench, .discovery-workspace, .schema-editor, .dataset-surface, table, pre, .observatory-request-story, a[href*=\"question=\"], a[href*=\"draft=\"], a[href*=\"sql=\"], a[href*=\"description=\"]",
      recordBody: false,
      recordHeaders: false,
      streamNetworkBody: false,
      maskCapturedNetworkRequestFn: request => ({ ...request, name: analyticsUrl(request.name) }),
    },
    before_send: event => {
      if (!event) return null
      event.properties = redactAnalyticsProperties(event.properties) as typeof event.properties
      // The first anonymous pageview may precede person creation. Mark it directly.
      if (internalTest) event.properties.$internal_or_test_user = true
      event.properties.analytics_version = 2
      event.properties.environment = environment
      event.properties.release = process.env.NEXT_PUBLIC_RELEASE ?? "local"
      return event
    },
  })
}
