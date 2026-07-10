import { useActionRun } from "@/hooks/use-action-run"
import type { CalibrateInput, CalibrationOutput } from "@/types/calibrate"

export function useCalibrateRun() {
  return useActionRun<CalibrateInput, CalibrationOutput | null>("/calibrate/", "Calibration", null)
}
