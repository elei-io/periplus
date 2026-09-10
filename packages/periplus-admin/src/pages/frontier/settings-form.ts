import type {
  FrontierControlView,
  FrontierSettings,
  ReplaceFrontierSettings,
} from "../../types/frontier"

type NumericKey = Exclude<
  keyof FrontierSettings,
  "paused" | "exclusions"
>
export const settingFields: {
  key: NumericKey
  label: string
  unit: number
  min: number
  max: number
}[] = [
  {
    key: "dispatch_limit",
    label: "Concurrent dispatches",
    unit: 1,
    min: 1,
    max: 10000,
  },
  {
    key: "capture_timeout_ms",
    label: "Capture timeout (seconds)",
    unit: 1000,
    min: 1000,
    max: 3600000,
  },
]

export type FrontierDraft = {
  version: number
  settings: FrontierSettings
  numbers: Record<NumericKey, string>
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
