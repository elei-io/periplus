import { HighlightStyle, syntaxHighlighting } from "@codemirror/language"
import { Prec } from "@codemirror/state"
import { EditorView } from "@codemirror/view"
import { tags } from "@lezer/highlight"

const sqlHighlightStyle = HighlightStyle.define([
  { tag: tags.keyword, color: "var(--sql-keyword)", fontWeight: "650" },
  { tag: tags.standard(tags.name), color: "var(--sql-builtin)" },
  { tag: tags.typeName, color: "var(--sql-type)" },
  { tag: tags.string, color: "var(--sql-string)" },
  { tag: tags.number, color: "var(--sql-number)" },
  { tag: tags.bool, color: "var(--sql-number)" },
  { tag: tags.null, color: "var(--sql-muted)", fontStyle: "italic" },
  { tag: tags.comment, color: "var(--sql-muted)", fontStyle: "italic" },
  { tag: tags.operator, color: "var(--sql-operator)" },
  { tag: tags.punctuation, color: "var(--sql-punctuation)" },
  { tag: tags.name, color: "var(--sql-name)" },
])

export const sqlSyntaxHighlighting = syntaxHighlighting(sqlHighlightStyle)

export const sqlEditorTheme = Prec.highest(
  EditorView.theme({
    "&": {
      backgroundColor: "transparent",
      color: "var(--card-foreground)",
      fontSize: "13px",
    },
    "&.cm-focused": { outline: "none" },
    ".cm-scroller": {
      fontFamily:
        "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      lineHeight: "1.75",
      overflow: "auto",
    },
    ".cm-content": {
      caretColor: "var(--foreground)",
      padding: "16px 0 28px",
    },
    ".cm-line": { padding: "0 18px" },
    ".cm-cursor, .cm-dropCursor": {
      borderLeftColor: "var(--foreground)",
      borderLeftWidth: "2px",
    },
    ".cm-selectionBackground": { backgroundColor: "transparent !important" },
    ".cm-content ::selection, .cm-line ::selection": {
      backgroundColor: "var(--sql-selection) !important",
      color: "var(--foreground) !important",
    },
    ".cm-gutters": {
      backgroundColor: "transparent",
      color: "var(--muted-foreground)",
      border: "none",
      padding: "16px 0 28px",
    },
    ".cm-activeLine": { backgroundColor: "var(--sql-active-line)" },
    ".cm-activeLineGutter": {
      backgroundColor: "var(--sql-active-line)",
      color: "var(--foreground)",
    },
    ".cm-tooltip": {
      overflow: "hidden",
      backgroundColor: "var(--popover)",
      color: "var(--popover-foreground)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius-lg)",
      boxShadow: "0 16px 40px oklch(0 0 0 / 22%)",
    },
    ".cm-tooltip-autocomplete > ul": {
      fontFamily: "var(--font-sans)",
      padding: "4px",
    },
    ".cm-tooltip-autocomplete > ul > li": {
      borderRadius: "6px",
      padding: "5px 8px",
    },
    ".cm-tooltip-autocomplete > ul > li[aria-selected]": {
      backgroundColor: "var(--accent)",
      color: "var(--accent-foreground)",
    },
    ".cm-completionIcon": { opacity: "0.65" },
    ".cm-completionLabel": {
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    },
    ".cm-completionDetail": {
      color: "var(--muted-foreground)",
      fontStyle: "normal",
    },
  })
)
