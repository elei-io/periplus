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
  onChange: (value: string) => void
  onRun: () => void
  views?: Array<{ view_name: string; columns: string[] }>
  materializedViews?: Array<{ name: string; columns: Array<{ name: string }> }>
}

export function SqlEditor({ value, onChange, onRun, views = [], materializedViews = [] }: SqlEditorProps) {
  const sqlLanguage = useMemo(() => {
    const tables = catalogueTables
    const viewTables = Object.fromEntries(
      views.map((view) => [view.view_name, view.columns])
    )
    const materializedTables = Object.fromEntries(
      materializedViews.map((view) => [view.name, view.columns.map((column) => column.name)])
    )
    const catalogueSchema = {
      ...tables,
      ...viewTables,
      main: tables,
      views: viewTables,
      materialized: materializedTables,
      atlas: { main: tables, views: viewTables, materialized: materializedTables },
    }
    return sql({
      dialect: PostgreSQL,
      schema: catalogueSchema,
      upperCaseKeywords: true,
    })
  }, [views, materializedViews])

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
      aria-label="Catalogue SQL editor"
      value={value}
      height="clamp(240px, 38vh, 360px)"
      theme="none"
      extensions={extensions}
      onChange={onChange}
      onKeyDown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
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
        autocompletion: true,
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
