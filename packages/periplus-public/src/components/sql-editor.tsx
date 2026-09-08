"use client"

import CodeMirror from "@uiw/react-codemirror"
import { sql, StandardSQL } from "@codemirror/lang-sql"
import { EditorView } from "@codemirror/view"
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language"
import { tags } from "@lezer/highlight"
import { memo } from "react"

const extensions = [
  sql({ dialect: StandardSQL, upperCaseKeywords: true, schema: {
    web: {
      observation: ["observation_id", "requested_url", "effective_url", "observed_at", "outcome", "http_status_code", "content_id", "capture_policy"],
      collection: ["collection_id", "requested_at", "specification", "settled_at", "outcome", "seed_provenance", "consumed_pages", "supplied_pages", "failed_pages"],
      fulfillment: ["fulfillment_id", "collection_id", "observation_id", "requested_url", "parent_observation_id", "depth", "rule_id", "mode", "decided_at"],
      acquisition_reason: ["reason_id", "observation_id", "collection_id", "parent_observation_id", "reason", "policy_version", "rule_id", "decided_at"],
      link_occurrence: ["observation_id", "content_id", "source_url", "target_url", "observed_at", "raw_href", "relation_scope"],
    },
    content: {
      object: ["content_id", "size_bytes", "detected_media_type", "content_format"],
      html_element: ["content_id", "element_index", "tag", "attributes", "text_direct", "text_tail", "parent_index", "depth"],
    },
  } }),
  EditorView.lineWrapping,
  EditorView.theme({
    "&": { backgroundColor: "var(--editor-background)", color: "#e3eee7", fontSize: "14px" },
    ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", lineHeight: "1.8" },
    ".cm-content": { padding: "18px 0", caretColor: "#bbf7d0" },
    ".cm-line": { padding: "0 20px" },
    ".cm-gutters": { backgroundColor: "var(--editor-background)", color: "#9caf9f", border: "none", paddingLeft: "12px" },
    ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "#ffffff05" },
    "&.cm-focused": { outline: "none" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { backgroundColor: "#3d6353 !important" },
    ".cm-cursor": { borderLeftColor: "#bbf7d0" },
    ".cm-tooltip": { backgroundColor: "#20382f", color: "#e3eee7", border: "1px solid #466858" },
    ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "#385b48", color: "white" },
    ".cm-search": { backgroundColor: "#20382f", color: "#e3eee7" },
  }, { dark: true }),
  syntaxHighlighting(HighlightStyle.define([
    { tag: tags.keyword, color: "#b7a8f5" },
    { tag: [tags.string, tags.special(tags.string)], color: "#c6df99" },
    { tag: tags.number, color: "#f5c58c" },
    { tag: tags.comment, color: "#a0b3a5", fontStyle: "italic" },
    { tag: [tags.operator, tags.punctuation], color: "#a5c9bb" },
    { tag: tags.function(tags.variableName), color: "#8edbd1" },
  ])),
]

const editableExtensions = [...extensions, EditorView.contentAttributes.of({ "aria-label": "SQL query", "aria-describedby": "editor-help" })]
const readonlyExtensions = [...extensions, EditorView.contentAttributes.of({ "aria-label": "SQL statement", tabindex: "0" })]
const editableSetup = { foldGutter: false, highlightActiveLine: true, autocompletion: true, bracketMatching: true }
const readonlySetup = { ...editableSetup, highlightActiveLine: false, autocompletion: false }

export const SqlEditor = memo(function SqlEditor({ value, onChange, onSelectionChange, readOnly = false }: { value: string; onChange?: (value: string) => void; onSelectionChange?: (value: string) => void; readOnly?: boolean }) {
  return <CodeMirror value={value} onChange={onChange} onUpdate={update => { if (update.selectionSet || update.docChanged) { const { from, to } = update.state.selection.main; onSelectionChange?.(update.state.sliceDoc(from, to)) } }} extensions={readOnly ? readonlyExtensions : editableExtensions} readOnly={readOnly} editable={!readOnly} height={readOnly ? "auto" : "340px"} maxHeight={readOnly ? "320px" : undefined} theme="none" indentWithTab={false} basicSetup={readOnly ? readonlySetup : editableSetup} />
})
