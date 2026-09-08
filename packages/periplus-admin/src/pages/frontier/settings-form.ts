import type {
  FrontierControlView,
  FrontierSettings,
  ReplaceFrontierSettings,
} from "../../types/frontier"

type NumericKey = Exclude<
  keyof FrontierSettings,
  "paused" | "exclusions" | "captures_per_minute"
>
export const settingFields: {
  key: NumericKey
  label: string
  unit: number
  min: number
  max: number
  group: "pace" | "budget" | "capacity"
}[] = [
  {
    key: "dispatch_limit",
    label: "Concurrent dispatches",
    unit: 1,
    min: 1,
    max: 10000,
    group: "pace",
  },
  {
    key: "capture_timeout_ms",
    label: "Capture timeout (seconds)",
    unit: 1000,
    min: 1000,
    max: 3600000,
    group: "pace",
  },
  {
    key: "attempt_allowance",
    label: "Total attempt allowance",
    unit: 1,
    min: 0,
    max: 1000000000,
    group: "budget",
  },
  {
    key: "capture_time_allowance_ms",
    label: "Total capture time (hours)",
    unit: 3600000,
    min: 0,
    max: 1000000000000,
    group: "budget",
  },
  {
    key: "admission_limit",
    label: "Pending acquisitions",
    unit: 1,
    min: 1,
    max: 1000000,
    group: "capacity",
  },
  {
    key: "acquisition_limit",
    label: "Retained acquisitions",
    unit: 1,
    min: 1,
    max: 1000000,
    group: "capacity",
  },
  {
    key: "collection_limit",
    label: "Retained collections",
    unit: 1,
    min: 1,
    max: 100000,
    group: "capacity",
  },
  {
    key: "interest_limit",
    label: "Retained collection URLs",
    unit: 1,
    min: 1,
    max: 10000000,
    group: "capacity",
  },
]

export type FrontierDraft = {
  version: number
  settings: FrontierSettings
  numbers: Record<NumericKey, string>
  rate: string
  unlimitedRate: boolean
}

export function settingsDraft(
  state: Pick<FrontierControlView, "policy_version" | "settings">
): FrontierDraft {
  return {
    version: state.policy_version,
    settings: structuredClone(state.settings),
    numbers: Object.fromEntries(
      settingFields.map((field) => [
        field.key,
        String(state.settings[field.key] / field.unit),
      ])
    ) as Record<NumericKey, string>,
    rate:
      state.settings.captures_per_minute === null
        ? ""
        : String(state.settings.captures_per_minute),
    unlimitedRate: state.settings.captures_per_minute === null,
  }
}

export function settingsPayload(draft: FrontierDraft): ReplaceFrontierSettings {
  const settings = structuredClone(draft.settings)
  for (const field of settingFields) {
    const raw = draft.numbers[field.key]
    const value = Number(raw) * field.unit
    if (
      !raw.trim() ||
      !Number.isFinite(value) ||
      Math.abs(value - Math.round(value)) > 0.000001 ||
      value < field.min ||
      value > field.max
    ) {
      throw new Error(
        `${field.label} must be between ${field.min / field.unit} and ${field.max / field.unit}${field.unit === 1 ? " in whole numbers" : ""}.`
      )
    }
    settings[field.key] = Math.round(value)
  }
  const rate = Number(draft.rate)
  if (
    !draft.unlimitedRate &&
    (!draft.rate.trim() || !Number.isInteger(rate) || rate < 1 || rate > 60000)
  ) {
    throw new Error(
      "Dispatches per minute must be a whole number between 1 and 60000."
    )
  }
  settings.captures_per_minute = draft.unlimitedRate ? null : rate
  if (
    settings.exclusions.length > 100 ||
    settings.exclusions.some(
      (rule) => !rule.host.trim() || !rule.path_prefix.startsWith("/")
    )
  ) {
    throw new Error(
      "Use at most 100 exclusions, each with a host and an absolute path."
    )
  }
  return { expected_version: draft.version, settings }
}
