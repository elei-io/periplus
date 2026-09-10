import posthog from "posthog-js"
import { analyticsUrl, redactAnalyticsProperties } from "./lib/analytics-privacy"

const projectToken = process.env.NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN
const apiHost = process.env.NEXT_PUBLIC_POSTHOG_HOST
const environment = process.env.NEXT_PUBLIC_ANALYTICS_ENVIRONMENT ?? "development"

if (projectToken && apiHost && environment === "production") {
  posthog.init(projectToken, {
    api_host: apiHost,
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
      blockSelector: ".sql-workbench, .discovery-conversation, .discovery-definition, table, pre, .observatory-request-story",
      maskTextSelector: ".sql-workbench, .discovery-conversation, .discovery-definition, table, pre, .observatory-request-story",
      recordBody: false,
      recordHeaders: false,
      streamNetworkBody: false,
      maskCapturedNetworkRequestFn: request => ({ ...request, name: analyticsUrl(request.name) }),
    },
    before_send: event => {
      if (!event) return null
      event.properties = redactAnalyticsProperties(event.properties) as typeof event.properties
      event.properties.environment = environment
      event.properties.release = process.env.NEXT_PUBLIC_RELEASE ?? "local"
      return event
    },
  })
}
