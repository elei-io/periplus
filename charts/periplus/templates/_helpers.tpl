{{- define "periplus.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "periplus.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "periplus.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "periplus.labels" -}}
helm.sh/chart: {{ include "periplus.chart" . }}
app.kubernetes.io/name: {{ include "periplus.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "periplus.selectorLabels" -}}
app.kubernetes.io/name: {{ include "periplus.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "periplus.componentSelectorLabels" -}}
{{ include "periplus.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "periplus.coreImage" -}}
{{- printf "%s:%s" .Values.images.core.repository (.Values.images.core.tag | default .Chart.AppVersion) }}
{{- end }}

{{- define "periplus.adminImage" -}}
{{- printf "%s:%s" .Values.images.admin.repository (.Values.images.admin.tag | default .Chart.AppVersion) }}
{{- end }}

{{- define "periplus.publicImage" -}}
{{- printf "%s:%s" .Values.images.public.repository (.Values.images.public.tag | default .Chart.AppVersion) }}
{{- end }}

{{- define "periplus.podPlacement" -}}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.topologySpreadConstraints }}
topologySpreadConstraints:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "periplus.coreEnv" -}}
- name: PERIPLUS_CONTROL_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.controlDatabase.name | quote }}
      key: {{ .Values.secrets.controlDatabase.urlKey | quote }}

- name: PERIPLUS_NATS_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.nats.name | quote }}
      key: {{ .Values.secrets.nats.urlKey | quote }}
- name: PERIPLUS_NATS_SEED
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.nats.name | quote }}
      key: {{ .Values.secrets.nats.seedKey | quote }}
- name: PERIPLUS_REPOSITORY_S3_ENDPOINT
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.endpointKey | quote }}
- name: PERIPLUS_REPOSITORY_S3_REGION
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.regionKey | quote }}
- name: PERIPLUS_REPOSITORY_S3_BUCKET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.bucketKey | quote }}
- name: PERIPLUS_REPOSITORY_S3_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.accessKeyIdKey | quote }}
- name: PERIPLUS_REPOSITORY_S3_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.secretAccessKeyKey | quote }}









- name: PERIPLUS_REPOSITORY_STORAGE
  value: s3
- name: PERIPLUS_REPOSITORY_S3_PREFIX
  value: {{ .Values.config.repository.prefix | quote }}
- name: PERIPLUS_REPOSITORY_S3_URL_STYLE
  value: {{ .Values.config.repository.s3UrlStyle | quote }}
- name: PERIPLUS_REPOSITORY_S3_USE_SSL
  value: {{ .Values.config.repository.s3UseSSL | quote }}
- name: PERIPLUS_NATS_OPERATIONAL_REPLICAS
  value: {{ .Values.config.nats.operationalReplicas | quote }}
- name: PERIPLUS_CATALOGUE_WORK_STREAM_REPLICAS
  value: {{ .Values.config.nats.catalogueWorkStreamReplicas | quote }}
- name: PERIPLUS_LOG_LEVEL
  value: {{ .Values.config.logLevel | quote }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{- define "periplus.envFrom" -}}
{{- with .Values.extraEnvFrom }}
envFrom:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "periplus.corePodSpec" -}}
automountServiceAccountToken: false
terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{ include "periplus.podPlacement" . }}
{{- end }}

{{- define "periplus.duckdbEnv" -}}
- name: PERIPLUS_DUCKDB_THREADS
  value: {{ .threads | quote }}
- name: PERIPLUS_DUCKDB_MEMORY_LIMIT
  value: {{ .memoryLimit | quote }}
- name: PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE
  value: {{ .maxTempDirectorySize | quote }}
{{- end }}

{{- define "periplus.scalingQuery" -}}
{{- $scope := printf "namespace=%q,periplus_release=%q,periplus_component=%q" .root.Release.Namespace .root.Release.Name .role -}}
{{- if eq .role "crawler" -}}
sum(avg_over_time(periplus_crawler_active_captures{ {{ $scope }} }[2m]) and (timestamp(periplus_crawler_active_captures{ {{ $scope }} }) > time() - 60))
{{- else if eq .role "query" -}}
sum(avg_over_time(periplus_query_active_operations{ {{ $scope }} }[2m]) and (timestamp(periplus_query_active_operations{ {{ $scope }} }) > time() - 60))
{{- else -}}
{{- fail (printf "%s.autoscaling.query is required for a custom Prometheus policy" .role) -}}
{{- end -}}
{{- end }}

{{- define "periplus.clickhouseWriterEnv" -}}
- name: PERIPLUS_CLICKHOUSE_URL
  value: {{ .Values.config.clickhouse.url | quote }}
- name: PERIPLUS_CLICKHOUSE_USER
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.clickhouse.name | quote }}
      key: {{ .Values.secrets.clickhouse.userKey | quote }}
- name: PERIPLUS_CLICKHOUSE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.clickhouse.name | quote }}
      key: {{ .Values.secrets.clickhouse.passwordKey | quote }}
{{- end }}

{{- define "periplus.clickhouseQueryEnv" -}}
- name: PERIPLUS_CLICKHOUSE_QUERY_WORKLOAD
  value: {{ .Values.config.clickhouse.queryWorkload | quote }}
- name: PERIPLUS_CLICKHOUSE_QUERY_USER
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.queryClickhouse.name | quote }}
      key: {{ .Values.secrets.queryClickhouse.userKey | quote }}
- name: PERIPLUS_CLICKHOUSE_QUERY_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.queryClickhouse.name | quote }}
      key: {{ .Values.secrets.queryClickhouse.passwordKey | quote }}
{{- end }}

{{- define "periplus.materializerImage" -}}
{{- .Values.materializer.image | default (include "periplus.coreImage" .) -}}
{{- end }}
