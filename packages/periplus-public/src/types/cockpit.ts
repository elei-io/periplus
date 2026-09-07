export interface CockpitItem {
  id: string
  url: string
  stage: "past" | "now" | "next"
  label: string
  started?: boolean
  origin?: string
}
