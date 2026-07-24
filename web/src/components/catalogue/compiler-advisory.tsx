import { Badge } from "@/components/ui/badge"
import type {
  CompilationDiagnostic,
  CompilationOutcome,
  DefinitionDependency,
} from "@/types/catalogue"

export function CompilerAdvisory({
  outcome,
  diagnostics,
  dependencies,
  compilerVersion,
  catalogueRevision,
}: {
  outcome: CompilationOutcome | null
  diagnostics: CompilationDiagnostic[]
  dependencies: DefinitionDependency[]
  compilerVersion: string | null
  catalogueRevision: string | null
}) {
  if (!outcome) return null
  return (
    <div className="rounded-xl border bg-card/60 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={outcome === "unsupported" ? "outline" : "secondary"}>
          Compiler · {outcome.replace("_", " ")}
        </Badge>
        <span className="text-xs text-muted-foreground">
          {compilerVersion}
          {catalogueRevision ? ` · catalogue ${catalogueRevision}` : ""}
        </span>
      </div>
      {diagnostics.length > 0 && (
        <div className="mt-3 space-y-2">
          {diagnostics.map((diagnostic, index) => (
            <div
              key={`${diagnostic.code}-${index}`}
              className="rounded-lg border bg-muted/30 px-3 py-2 text-xs"
            >
              <span className="font-medium">{diagnostic.code}</span>
              <span className="ml-2 text-muted-foreground">
                {diagnostic.message}
              </span>
            </div>
          ))}
        </div>
      )}
      {dependencies.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {dependencies.map((dependency) => (
            <Badge
              key={`${dependency.kind}:${dependency.path.join(">")}`}
              variant="outline"
              title={dependency.path.join(" → ")}
            >
              {dependency.qualified_name}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}
