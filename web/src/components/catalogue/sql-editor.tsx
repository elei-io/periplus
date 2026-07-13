import { completeFromList } from "@codemirror/autocomplete"
import { PostgreSQL, sql } from "@codemirror/lang-sql"
import { EditorView } from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import { useMemo } from "react"

import {
  catalogueFunctions,
  catalogueTables,
} from "@/components/catalogue/catalogue-schema"
import {
  sqlEditorTheme,
  sqlSyntaxHighlighting,
} from "@/components/catalogue/sql-editor-theme"

type SqlEditorProps = {
  value: string
  onChange?: (value: string) => void
  onRun?: () => void
  readOnly?: boolean
  height?: string
  ariaLabel?: string
  views?: Array<{ view_name: string; columns: string[] }>
}

export function SqlEditor({ value, onChange = () => undefined, onRun, readOnly = false, height = "clamp(240px, 38vh, 360px)", ariaLabel = "Catalogue SQL editor", views = [] }: SqlEditorProps) {
  const sqlLanguage = useMemo(() => {
    const tables = catalogueTables
    const viewTables = Object.fromEntries(
      views.map((view) => [view.view_name, view.columns])
    )
    const catalogueSchema = {
      ...tables,
      ...viewTables,
      main: tables,
      views: viewTables,
      atlas: { main: tables, views: viewTables },
    }
    return sql({
      dialect: PostgreSQL,
      schema: catalogueSchema,
      upperCaseKeywords: true,
    })
  }, [views])

  const extensions = useMemo(
    () => [
      sqlLanguage,
      sqlLanguage.language.data.of({
        autocomplete: completeFromList([...catalogueFunctions]),
      }),
      sqlSyntaxHighlighting,
      sqlEditorTheme,
      EditorView.lineWrapping,
    ],
    [sqlLanguage]
  )

  return (
    <CodeMirror
      aria-label={ariaLabel}
      value={value}
      height={height}
      theme="none"
      extensions={extensions}
      editable={!readOnly}
      onChange={onChange}
      onKeyDown={(event) => {
        if (onRun && (event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault()
          onRun()
        }
      }}
      basicSetup={{
        lineNumbers: true,
        foldGutter: false,
        dropCursor: false,
        allowMultipleSelections: false,
        indentOnInput: true,
        bracketMatching: true,
        closeBrackets: true,
        autocompletion: !readOnly,
        highlightSelectionMatches: false,
        highlightActiveLine: true,
        highlightActiveLineGutter: true,
        syntaxHighlighting: false,
        highlightSpecialChars: false,
        drawSelection: false,
      }}
    />
  )
}
