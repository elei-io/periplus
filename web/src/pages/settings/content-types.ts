export const contentTypeGroups = [
  {
    id: "pages",
    label: "Web pages",
    description: "Keep HTML and XHTML pages for DOM extraction and links.",
    types: ["text/html", "application/xhtml+xml"],
  },
  {
    id: "images",
    label: "Images",
    description: "Keep image downloads of any supported format.",
    types: ["image/*"],
  },
  {
    id: "pdfs",
    label: "PDFs",
    description: "Keep PDF documents as exact raw artifacts.",
    types: ["application/pdf"],
  },
  {
    id: "word",
    label: "Word documents",
    description: "Keep DOCX and legacy DOC downloads.",
    types: [
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "application/msword",
    ],
  },
  {
    id: "spreadsheets",
    label: "Spreadsheets",
    description: "Keep XLSX, XLS, and CSV downloads.",
    types: [
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "application/vnd.ms-excel",
      "text/csv",
    ],
  },
  {
    id: "presentations",
    label: "Presentations",
    description: "Keep PPTX and legacy PPT downloads.",
    types: [
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      "application/vnd.ms-powerpoint",
    ],
  },
  {
    id: "text-data",
    label: "Text and data",
    description: "Keep plain text, JSON, and XML downloads.",
    types: ["text/plain", "application/json", "application/xml", "text/xml"],
  },
  {
    id: "archives",
    label: "Archives",
    description: "Keep ZIP, gzip, and 7-Zip downloads.",
    types: [
      "application/zip",
      "application/gzip",
      "application/x-7z-compressed",
    ],
  },
  {
    id: "audio",
    label: "Audio",
    description: "Keep audio downloads of any supported format.",
    types: ["audio/*"],
  },
  {
    id: "video",
    label: "Video",
    description: "Keep video downloads of any supported format.",
    types: ["video/*"],
  },
] as const

export type ContentTypeGroupId = (typeof contentTypeGroups)[number]["id"]

export function parseContentTypes(value: string) {
  return [
    ...new Set(
      value
        .split(",")
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean)
    ),
  ]
}

export function contentTypeGroupState(
  value: string,
  groupId: ContentTypeGroupId
) {
  const selected = new Set(parseContentTypes(value))
  const group = contentTypeGroups.find((candidate) => candidate.id === groupId)
  if (!group) return "off" as const
  const count = group.types.filter((type) => selected.has(type)).length
  if (count === 0) return "off" as const
  return count === group.types.length ? ("on" as const) : ("partial" as const)
}

export function setContentTypeGroup(
  value: string,
  groupId: ContentTypeGroupId,
  enabled: boolean
) {
  const selected = new Set(parseContentTypes(value))
  const group = contentTypeGroups.find((candidate) => candidate.id === groupId)
  if (!group) return [...selected].join(", ")
  for (const type of group.types) {
    if (enabled) selected.add(type)
    else selected.delete(type)
  }
  return [...selected].join(", ")
}
